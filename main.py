"""
Revenue Opportunity Scanner — Telegram Bot
Main entry point. Interactive interface for managing revenue opportunities.

Commands:
/scan — Run full 6-month scan (takes a few minutes)
/plan — Get today's action plan
/pipeline — View active opportunities pipeline
/opp <id> — Deep dive into specific opportunity  
/done <id> — Mark opportunity as completed
/skip <id> [reason] — Skip opportunity
/start <id> — Mark opportunity as in progress
/stats — Revenue statistics
/projects — View by project
/profile — View your behavioral profile
/help — Show commands
"""
import os
import json
import asyncio
import logging
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)
from telegram.constants import ParseMode

import database as db
from scanner import scan_all_work_chats, chunk_messages, format_messages_for_analysis
from analyzer import analyze_chat, generate_daily_plan, analyze_single_opportunity

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OWNER_ID = int(os.getenv("TELEGRAM_OWNER_ID", "271065518"))

pool = None


def owner_only(func):
    """Decorator to restrict commands to owner."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != OWNER_ID:
            await update.message.reply_text("⛔ Доступ только для владельца.")
            return
        return await func(update, context)
    return wrapper


async def post_init(app: Application):
    """Initialize database pool after app starts."""
    global pool
    pool = await db.get_pool()
    await db.init_db(pool)
    logger.info("Database initialized.")


# ═══════════════════════════════════════════
# COMMANDS
# ═══════════════════════════════════════════

@owner_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔍 <b>Revenue Opportunity Scanner</b>\n\n"
        "Я анализирую твои рабочие переписки и нахожу возможности для заработка.\n\n"
        "Команды:\n"
        "/scan — Полный скан переписок (6 мес)\n"
        "/plan — План действий на сегодня\n"
        "/pipeline — Активные возможности\n"
        "/opp [id] — Подробнее про возможность\n"
        "/done [id] — Отметить выполненной\n"
        "/skip [id] — Пропустить\n"
        "/stats — Статистика дохода\n"
        "/projects — По проектам\n"
        "/profile — Твой профиль\n"
        "/help — Эта справка",
        parse_mode=ParseMode.HTML
    )


@owner_only
async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Run full 6-month scan of work chats."""
    msg = await update.message.reply_text(
        "🔄 Запускаю скан рабочих чатов за 6 месяцев...\n"
        "Это займёт несколько минут. Я напишу когда закончу."
    )
    
    try:
        # Record scan
        scan_id = await db.save_scan(pool, "full_scan")
        
        # Step 1: Pull chat history
        await msg.edit_text("📡 Подключаюсь к Telegram и вытягиваю переписки...")
        scan_data = await scan_all_work_chats()
        
        total_chats = len(scan_data["chats"])
        total_messages = scan_data["total_messages"]
        
        await msg.edit_text(
            f"📡 Найдено {total_chats} рабочих чатов, {total_messages} сообщений.\n"
            f"🧠 Запускаю AI-анализ..."
        )
        
        # Step 2: Analyze each chat with Claude
        all_opportunities = 0
        all_profile_insights = {}
        
        for i, (chat_name, chat_data) in enumerate(scan_data["chats"].items(), 1):
            await msg.edit_text(
                f"🧠 Анализирую чат {i}/{total_chats}: {chat_name}\n"
                f"({chat_data['message_count']} сообщений)"
            )
            
            # Chunk messages if too many
            messages = chat_data["messages"]
            chunks = chunk_messages(messages, chunk_size=150)
            
            for chunk in chunks:
                messages_text = format_messages_for_analysis(chunk)
                
                # Skip very short chunks
                if len(messages_text) < 100:
                    continue
                
                result = await analyze_chat(chat_name, messages_text)
                
                # Save opportunities
                for opp in result.get("opportunities", []):
                    opp["source_chat"] = chat_name
                    if chunk:
                        opp["source_date"] = chunk[0].get("date")
                    
                    # Check for duplicates
                    is_dup = await db.check_duplicate(pool, opp["title"], chat_name)
                    if not is_dup:
                        await db.save_opportunity(pool, opp)
                        all_opportunities += 1
                
                # Merge profile insights
                insights = result.get("profile_insights", {})
                for key, value in insights.items():
                    if value:
                        await db.save_profile_insight(pool, f"{chat_name}_{key}", value)
                        all_profile_insights[key] = value
                
                # Small delay to avoid rate limits
                await asyncio.sleep(1)
        
        # Complete scan record
        await db.complete_scan(pool, scan_id, total_chats, total_messages, all_opportunities)
        
        # Summary
        stats = await db.get_stats(pool)
        await msg.edit_text(
            f"✅ <b>Скан завершён!</b>\n\n"
            f"📊 Проанализировано:\n"
            f"  • {total_chats} чатов\n"
            f"  • {total_messages} сообщений\n"
            f"  • За {scan_data['scan_period']}\n\n"
            f"💡 Найдено новых возможностей: <b>{all_opportunities}</b>\n"
            f"💰 Pipeline: ${stats['revenue_pipeline_low']}-${stats['revenue_pipeline_high']}\n\n"
            f"Жми /plan чтобы получить план действий на сегодня!",
            parse_mode=ParseMode.HTML
        )
        
    except Exception as e:
        logger.error(f"Scan failed: {e}", exc_info=True)
        await msg.edit_text(f"❌ Ошибка при скане: {str(e)[:200]}")


@owner_only
async def cmd_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate today's action plan."""
    msg = await update.message.reply_text("🧠 Генерирую план на сегодня...")
    
    try:
        opportunities = await db.get_active_opportunities(pool, limit=15)
        if not opportunities:
            await msg.edit_text(
                "📭 Нет активных возможностей в pipeline.\n"
                "Запусти /scan чтобы проанализировать переписки."
            )
            return
        
        profile = await db.get_profile(pool)
        stats = await db.get_stats(pool)
        
        plan = await generate_daily_plan(
            [dict(o) for o in opportunities],
            profile,
            stats
        )
        
        # Add interactive buttons for top opportunities
        keyboard = []
        for opp in opportunities[:5]:
            keyboard.append([
                InlineKeyboardButton(f"✅ #{opp['id']}", callback_data=f"done_{opp['id']}"),
                InlineKeyboardButton(f"⏭ #{opp['id']}", callback_data=f"skip_{opp['id']}"),
                InlineKeyboardButton(f"🔍 #{opp['id']}", callback_data=f"detail_{opp['id']}")
            ])
        
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        
        # Split long messages
        if len(plan) > 4000:
            parts = [plan[i:i+4000] for i in range(0, len(plan), 4000)]
            await msg.edit_text(parts[0])
            for part in parts[1:]:
                await update.message.reply_text(part, reply_markup=reply_markup)
        else:
            await msg.edit_text(plan, reply_markup=reply_markup)
            
    except Exception as e:
        logger.error(f"Plan generation failed: {e}", exc_info=True)
        await msg.edit_text(f"❌ Ошибка: {str(e)[:200]}")


@owner_only
async def cmd_pipeline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show active opportunities pipeline."""
    opportunities = await db.get_active_opportunities(pool, limit=20)
    
    if not opportunities:
        await update.message.reply_text(
            "📭 Pipeline пуст. Запусти /scan для анализа."
        )
        return
    
    text = "📋 <b>АКТИВНЫЙ PIPELINE</b>\n\n"
    
    total_low = 0
    total_high = 0
    
    for opp in opportunities:
        status_emoji = "🆕" if opp["status"] == "new" else "🔄"
        conf_emoji = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(opp["confidence"], "⚪")
        
        text += (
            f"{status_emoji} <b>#{opp['id']}</b> {opp['title']}\n"
            f"   {conf_emoji} {opp['project']} | ${opp['revenue_low']}-${opp['revenue_high']}\n"
            f"   👤 {opp.get('contact_person', '-')} {opp.get('contact_handle', '')}\n\n"
        )
        total_low += opp["revenue_low"]
        total_high += opp["revenue_high"]
    
    text += f"\n💰 <b>Итого pipeline: ${total_low}-${total_high}</b>"
    text += f"\n\n/opp [id] — подробнее | /done [id] — выполнено | /skip [id] — пропустить"
    
    # Split if too long
    if len(text) > 4000:
        parts = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for part in parts:
            await update.message.reply_text(part, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)


@owner_only
async def cmd_opp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deep dive into a specific opportunity."""
    if not context.args:
        await update.message.reply_text("Укажи ID: /opp 5")
        return
    
    try:
        opp_id = int(context.args[0].replace("#", ""))
    except ValueError:
        await update.message.reply_text("ID должен быть числом: /opp 5")
        return
    
    opp = await db.get_opportunity_by_id(pool, opp_id)
    if not opp:
        await update.message.reply_text(f"Возможность #{opp_id} не найдена.")
        return
    
    actions = json.loads(opp["action_items"]) if isinstance(opp["action_items"], str) else opp["action_items"]
    actions_text = "\n".join(f"  {i}. {a}" for i, a in enumerate(actions, 1))
    
    text = (
        f"🔍 <b>Возможность #{opp['id']}</b>\n\n"
        f"📌 <b>{opp['title']}</b>\n"
        f"🏷 Проект: {opp['project']}\n"
        f"💰 Потенциал: {opp['potential_revenue']}\n"
        f"   (${opp['revenue_low']}-${opp['revenue_high']})\n"
        f"🎯 Уверенность: {opp['confidence']}\n"
        f"👤 Контакт: {opp.get('contact_person', '-')} {opp.get('contact_handle', '')}\n\n"
        f"📝 <b>Описание:</b>\n{opp['description']}\n\n"
        f"✅ <b>Шаги:</b>\n{actions_text}\n\n"
        f"💡 <b>Почему это для тебя:</b>\n{opp.get('reasoning', '-')}\n\n"
        f"📎 <b>Источник:</b> {opp.get('source_chat', '-')}\n"
        f"💬 <i>{opp.get('source_snippet', '')}</i>"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("✅ Выполнено", callback_data=f"done_{opp['id']}"),
            InlineKeyboardButton("⏭ Пропустить", callback_data=f"skip_{opp['id']}"),
        ],
        [
            InlineKeyboardButton("🔄 В работе", callback_data=f"progress_{opp['id']}"),
            InlineKeyboardButton("🧠 Глубокий анализ", callback_data=f"analyze_{opp['id']}"),
        ]
    ]
    
    if len(text) > 4000:
        text = text[:3950] + "..."
    
    await update.message.reply_text(
        text, 
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


@owner_only
async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mark opportunity as done."""
    if not context.args:
        await update.message.reply_text("Укажи ID: /done 5")
        return
    opp_id = int(context.args[0].replace("#", ""))
    await db.mark_done(pool, opp_id)
    opp = await db.get_opportunity_by_id(pool, opp_id)
    await update.message.reply_text(
        f"✅ #{opp_id} отмечена выполненной!\n"
        f"💰 +${opp['revenue_low']}-${opp['revenue_high']} в реализованный доход"
    )


@owner_only
async def cmd_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Skip opportunity."""
    if not context.args:
        await update.message.reply_text("Укажи ID: /skip 5 [причина]")
        return
    opp_id = int(context.args[0].replace("#", ""))
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else None
    await db.mark_skipped(pool, opp_id, reason)
    await update.message.reply_text(f"⏭ #{opp_id} пропущена.")


@owner_only  
async def cmd_progress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mark opportunity as in progress."""
    if not context.args:
        await update.message.reply_text("Укажи ID: /start 5")
        return
    opp_id = int(context.args[0].replace("#", ""))
    await db.mark_in_progress(pool, opp_id)
    await update.message.reply_text(f"🔄 #{opp_id} — в работе!")


@owner_only
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show revenue statistics."""
    stats = await db.get_stats(pool)
    
    # Calculate goal progress
    realized_avg = (stats["revenue_realized_low"] + stats["revenue_realized_high"]) / 2
    goals_total = 15000  # 2026 goals
    progress = (realized_avg / goals_total * 100) if goals_total > 0 else 0
    
    text = (
        f"📊 <b>СТАТИСТИКА ДОХОДА</b>\n\n"
        f"🆕 Новых возможностей: {stats['new_count']}\n"
        f"🔄 В работе: {stats['in_progress']}\n"
        f"✅ Выполнено: {stats['done_count']}\n"
        f"⏭ Пропущено: {stats['skipped_count']}\n\n"
        f"💰 <b>Pipeline:</b> ${stats['revenue_pipeline_low']:,}-${stats['revenue_pipeline_high']:,}\n"
        f"✅ <b>Реализовано:</b> ${stats['revenue_realized_low']:,}-${stats['revenue_realized_high']:,}\n\n"
        f"🎯 <b>Прогресс к целям 2026 ($15K):</b>\n"
        f"{'█' * int(progress/5)}{'░' * (20 - int(progress/5))} {progress:.1f}%\n\n"
    )
    
    if realized_avg > 0:
        if realized_avg >= 2000:
            text += "✈️ Китай — ✅ можно ехать!\n"
        else:
            text += f"✈️ Китай ($2K) — осталось ${2000 - realized_avg:,.0f}\n"
        
        remaining = goals_total - realized_avg
        text += f"💻 MacBook ($3.5K) — осталось ${max(0, 3500 - max(0, realized_avg - 2000)):,.0f}\n"
    
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


@owner_only
async def cmd_projects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View opportunities grouped by project."""
    projects = ["zbs_media", "plan_banan", "savecharvak", "commercial", "trabaja", "general"]
    
    text = "🏢 <b>ВОЗМОЖНОСТИ ПО ПРОЕКТАМ</b>\n\n"
    
    for project in projects:
        opps = await db.get_active_opportunities(pool, limit=5, project=project)
        if opps:
            emoji = {
                "zbs_media": "📺", "plan_banan": "🍌", "savecharvak": "🌿",
                "commercial": "🎬", "trabaja": "💼", "general": "📌"
            }.get(project, "📌")
            
            total_low = sum(o["revenue_low"] for o in opps)
            total_high = sum(o["revenue_high"] for o in opps)
            
            text += f"{emoji} <b>{project}</b> ({len(opps)} возм.) — ${total_low}-${total_high}\n"
            for opp in opps[:3]:
                text += f"  • #{opp['id']} {opp['title'][:50]}\n"
            if len(opps) > 3:
                text += f"  ... и ещё {len(opps)-3}\n"
            text += "\n"
    
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


@owner_only
async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show behavioral profile insights."""
    profile = await db.get_profile(pool)
    
    if not profile:
        await update.message.reply_text(
            "📭 Профиль ещё не сформирован. Запусти /scan."
        )
        return
    
    text = "🧠 <b>ТВОЙ ПРОФИЛЬ</b>\n<i>(из анализа переписок)</i>\n\n"
    
    # Group by type
    categories = {}
    for key, value in profile.items():
        parts = key.rsplit("_", 1)
        if len(parts) == 2:
            chat, category = parts[0], parts[1]
        else:
            category = key
            chat = "general"
        
        if category not in categories:
            categories[category] = []
        categories[category].append({"chat": chat, "insight": value})
    
    category_names = {
        "style": "💬 Стиль общения",
        "patterns": "⚡ Паттерны энергии", 
        "spots": "🔴 Слепые зоны",
        "strengths": "💪 Сильные стороны"
    }
    
    for cat, items in categories.items():
        cat_name = category_names.get(cat, f"📌 {cat}")
        text += f"\n{cat_name}:\n"
        for item in items[:3]:
            text += f"• {item['insight'][:200]}\n"
    
    if len(text) > 4000:
        text = text[:3950] + "..."
    
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ═══════════════════════════════════════════
# CALLBACK HANDLERS (inline buttons)
# ═══════════════════════════════════════════

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button presses."""
    query = update.callback_query
    
    if query.from_user.id != OWNER_ID:
        await query.answer("⛔ Только владелец!")
        return
    
    data = query.data
    await query.answer()
    
    if data.startswith("done_"):
        opp_id = int(data.replace("done_", ""))
        await db.mark_done(pool, opp_id)
        opp = await db.get_opportunity_by_id(pool, opp_id)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            f"✅ #{opp_id} выполнена! +${opp['revenue_low']}-${opp['revenue_high']}"
        )
        
    elif data.startswith("skip_"):
        opp_id = int(data.replace("skip_", ""))
        await db.mark_skipped(pool, opp_id)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(f"⏭ #{opp_id} пропущена")
        
    elif data.startswith("progress_"):
        opp_id = int(data.replace("progress_", ""))
        await db.mark_in_progress(pool, opp_id)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(f"🔄 #{opp_id} в работе!")
        
    elif data.startswith("detail_"):
        opp_id = int(data.replace("detail_", ""))
        opp = await db.get_opportunity_by_id(pool, opp_id)
        if opp:
            actions = json.loads(opp["action_items"]) if isinstance(opp["action_items"], str) else opp["action_items"]
            text = (
                f"🔍 <b>#{opp['id']}: {opp['title']}</b>\n\n"
                f"{opp['description']}\n\n"
                f"💰 {opp['potential_revenue']}\n"
                f"👤 {opp.get('contact_person', '-')} {opp.get('contact_handle', '')}\n\n"
                f"✅ Шаги:\n" + "\n".join(f"  {i}. {a}" for i, a in enumerate(actions, 1))
            )
            if len(text) > 4000:
                text = text[:3950] + "..."
            await query.message.reply_text(text, parse_mode=ParseMode.HTML)
    
    elif data.startswith("analyze_"):
        opp_id = int(data.replace("analyze_", ""))
        opp = await db.get_opportunity_by_id(pool, opp_id)
        if opp:
            await query.message.reply_text("🧠 Делаю глубокий анализ...")
            opp_text = f"{opp['title']}\n{opp['description']}\nПотенциал: {opp['potential_revenue']}\nКонтакт: {opp.get('contact_person', '-')}"
            analysis = await analyze_single_opportunity(opp_text)
            if len(analysis) > 4000:
                parts = [analysis[i:i+4000] for i in range(0, len(analysis), 4000)]
                for part in parts:
                    await query.message.reply_text(part)
            else:
                await query.message.reply_text(analysis)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help."""
    await cmd_start(update, context)


# ═══════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════

def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("plan", cmd_plan))
    app.add_handler(CommandHandler("pipeline", cmd_pipeline))
    app.add_handler(CommandHandler("opp", cmd_opp))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(CommandHandler("skip", cmd_skip))
    app.add_handler(CommandHandler("progress", cmd_progress))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("projects", cmd_projects))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(callback_handler))
    
    logger.info("Revenue Opportunity Scanner bot starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

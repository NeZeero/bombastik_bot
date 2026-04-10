from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime, timedelta
from html import escape

from database import (
    add_slot,
    delete_slot,
    get_slots_for_date,
    get_all_bookings,
    get_booked_dates,
    get_bookings_for_date,
    cancel_booking,
)
from config import MASTER_CONTACT, is_master

router = Router()

def _kb_pick_day(prefix: str) -> InlineKeyboardMarkup:
    """Календарь на 30 дней вперёд (для мастера)."""
    today = datetime.now().date()
    buttons = []
    row = []
    for i in range(0, 30):
        d = today + timedelta(days=i)
        label = d.strftime("%d.%m")
        cb = f"{prefix}:{d.strftime('%Y-%m-%d')}"
        row.append(InlineKeyboardButton(text=label, callback_data=cb))
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="m_back")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


class MasterFSM(StatesGroup):
    waiting_times = State()


def master_only(message: Message) -> bool:
    return is_master(message.from_user.id)


# ── Вход в панель мастера ─────────────────────────────────
@router.message(Command("master"))
async def master_panel(message: Message):
    if not master_only(message):
        return
    await message.answer(
        "👩‍💼 <b>Панель мастера</b>\n\n"
        "Выберите действие:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📅 Добавить рабочие дни", callback_data="m_add_days")],
            [InlineKeyboardButton(text="🗑 Удалить день/слот", callback_data="m_del_slot")],
            [InlineKeyboardButton(text="❌ Отмена записи", callback_data="m_cancel")],
            [InlineKeyboardButton(text="📋 Все записи", callback_data="m_bookings")],
        ])
    )


# ── Добавить рабочие дни ──────────────────────────────────
@router.callback_query(F.data == "m_add_days")
async def add_days_start(call: CallbackQuery):
    if not is_master(call.from_user.id):
        return
    await call.message.edit_text(
        "📅 Нажмите на день, чтобы добавить часы работы:",
        reply_markup=_kb_pick_day(prefix="mday")
    )


@router.callback_query(F.data.startswith("mday:"))
async def add_day_times(call: CallbackQuery, state: FSMContext):
    if not is_master(call.from_user.id):
        return
    date = call.data.split(":")[1]
    await state.update_data(master_date=date)
    await call.message.edit_text(
        f"🕐 Введите часы работы для <b>{datetime.strptime(date, '%Y-%m-%d').strftime('%d.%m.%Y')}</b>\n\n"
        "Отправьте через запятую, например:\n"
        "<code>10:00, 11:00, 12:00, 14:00, 15:30, 17:00</code>",
        parse_mode="HTML"
    )
    await state.set_state(MasterFSM.waiting_times)


@router.message(MasterFSM.waiting_times)
async def save_times(message: Message, state: FSMContext):
    if not is_master(message.from_user.id):
        return
    data = await state.get_data()
    date = data.get("master_date")

    raw = message.text.replace(" ", "").split(",")
    added = []
    errors = []
    for t in raw:
        t = t.strip()
        try:
            datetime.strptime(t, "%H:%M")
            add_slot(date, t)
            added.append(t)
        except ValueError:
            errors.append(t)

    date_label = datetime.strptime(date, "%Y-%m-%d").strftime("%d.%m.%Y")
    msg = f"✅ Добавлено на <b>{date_label}</b>: {', '.join(added)}"
    if errors:
        msg += f"\n\n⚠️ Не распознано: {', '.join(errors)} (используйте формат ЧЧ:ММ)"

    await message.answer(msg, parse_mode="HTML")
    await state.clear()

    # Сразу вернуть к выбору других дней (удобнее при составлении расписания)
    await message.answer(
        "📅 Выберите следующий день, чтобы добавить часы работы:",
        reply_markup=_kb_pick_day(prefix="mday")
    )


# ── Удалить слот ──────────────────────────────────────────
@router.callback_query(F.data == "m_del_slot")
async def del_slot_start(call: CallbackQuery):
    if not is_master(call.from_user.id):
        return
    await call.message.edit_text(
        "Выберите день для удаления слотов:",
        reply_markup=_kb_pick_day(prefix="mdelday")
    )


# ── Отмена записи ─────────────────────────────────────────
@router.callback_query(F.data == "m_cancel")
async def cancel_start(call: CallbackQuery):
    if not is_master(call.from_user.id):
        return

    dates = get_booked_dates()
    if not dates:
        await call.message.edit_text(
            "❌ Сейчас нет активных записей, которые можно отменить.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="m_back")]
            ])
        )
        return

    buttons = [
        [InlineKeyboardButton(
            text=datetime.strptime(d, "%Y-%m-%d").strftime("%d.%m.%Y"),
            callback_data=f"mcday:{d}"
        )]
        for d in dates
    ]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="m_back")])
    await call.message.edit_text(
        "❌ Выберите дату, чтобы отменить запись:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@router.callback_query(F.data.startswith("mcday:"))
async def cancel_pick_day(call: CallbackQuery):
    if not is_master(call.from_user.id):
        return
    date = call.data.split(":", 1)[1]
    rows = get_bookings_for_date(date)
    if not rows:
        await call.answer("На этот день уже нет записей.", show_alert=True)
        return

    date_label = datetime.strptime(date, "%Y-%m-%d").strftime("%d.%m.%Y")
    buttons = []
    for booking_id, time, name, phone, client_id in rows:
        buttons.append([
            InlineKeyboardButton(
                text=f"❌ {time} — {name}",
                callback_data=f"mcancel:{booking_id}"
            )
        ])
    buttons.append([InlineKeyboardButton(text="◀️ Назад к датам", callback_data="m_cancel")])
    buttons.append([InlineKeyboardButton(text="🏠 В панель мастера", callback_data="m_back")])
    await call.message.edit_text(
        f"❌ Записи на <b>{date_label}</b>:\n\nВыберите окно для отмены:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@router.callback_query(F.data.startswith("mcancel:"))
async def cancel_do(call: CallbackQuery):
    if not is_master(call.from_user.id):
        return

    booking_id_str = call.data.split(":", 1)[1]
    try:
        booking_id = int(booking_id_str)
    except ValueError:
        await call.answer("Некорректная запись.", show_alert=True)
        return

    row = cancel_booking(booking_id)
    if not row:
        await call.answer("Эта запись уже отменена.", show_alert=True)
        return

    date, time, name, phone, client_id = row
    try:
        date_label = datetime.strptime(date, "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        date_label = date

    await call.answer("✅ Запись отменена", show_alert=True)
    await call.message.edit_text(
        f"✅ Отменено: <b>{date_label}</b> {time} — {name} ({phone})\n\n"
        "Хотите отменить ещё одну запись?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Ещё отмена", callback_data="m_cancel")],
            [InlineKeyboardButton(text="🏠 В панель мастера", callback_data="m_back")],
        ])
    )

    if client_id:
        try:
            await call.bot.send_message(
                client_id,
                "❌ Ваша запись была отменена.\n"
                "Для повторной записи обратитесь к боту.\n"
                f"По вопросам: {MASTER_CONTACT}"
            )
        except Exception:
            pass


@router.callback_query(F.data.startswith("mdelday:"))
async def del_day_slots(call: CallbackQuery):
    if not is_master(call.from_user.id):
        return
    date = call.data.split(":")[1]
    slots = get_slots_for_date(date)
    if not slots:
        await call.answer("На этот день нет слотов.", show_alert=True)
        return

    buttons = []
    for time, is_booked in slots:
        status = "🔒" if is_booked else "🗑"
        cb = "noop" if is_booked else f"mdelslot:{date}:{time}"
        buttons.append([InlineKeyboardButton(text=f"{status} {time}", callback_data=cb)])
    buttons.append([InlineKeyboardButton(text="◀️ Назад к дням", callback_data="m_del_slot")])
    buttons.append([InlineKeyboardButton(text="🏠 В панель мастера", callback_data="m_back")])

    date_label = datetime.strptime(date, "%Y-%m-%d").strftime("%d.%m.%Y")
    await call.message.edit_text(
        f"🗑 Слоты на <b>{date_label}</b>:\n🔒 — есть запись (нельзя удалить)",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("mdelslot:"))
async def confirm_del_slot(call: CallbackQuery):
    if not is_master(call.from_user.id):
        return
    # "mdelslot:YYYY-MM-DD:HH:MM" — split с лимитом, иначе время ломается на "10" и "00"
    _, date, time = call.data.split(":", 2)
    delete_slot(date, time)
    await call.answer(f"✅ Слот {time} удалён", show_alert=True)
    # Обновить список
    slots = get_slots_for_date(date)
    if not slots:
        date_label = datetime.strptime(date, "%Y-%m-%d").strftime("%d.%m.%Y")
        await call.message.edit_text(
            f"✅ <b>Все слоты на {date_label} удалены.</b>\n\n"
            "Выберите другой день или вернитесь в панель:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад к дням", callback_data="m_del_slot")],
                [InlineKeyboardButton(text="🏠 В панель мастера", callback_data="m_back")],
            ]),
        )
        return
    buttons = []
    for t, is_booked in slots:
        status = "🔒" if is_booked else "🗑"
        cb = "noop" if is_booked else f"mdelslot:{date}:{t}"
        buttons.append([InlineKeyboardButton(text=f"{status} {t}", callback_data=cb)])
    buttons.append([InlineKeyboardButton(text="◀️ Назад к дням", callback_data="m_del_slot")])
    buttons.append([InlineKeyboardButton(text="🏠 В панель мастера", callback_data="m_back")])
    date_label = datetime.strptime(date, "%Y-%m-%d").strftime("%d.%m.%Y")
    await call.message.edit_text(
        f"🗑 Слоты на <b>{date_label}</b>:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "noop")
async def noop(call: CallbackQuery):
    await call.answer("Этот слот занят — удалить нельзя.", show_alert=True)


# ── Все записи ────────────────────────────────────────────
@router.callback_query(F.data == "m_bookings")
async def show_bookings(call: CallbackQuery):
    if not is_master(call.from_user.id):
        return
    rows = get_all_bookings()
    if not rows:
        await call.message.edit_text(
            "📋 <b>Все записи:</b>\n\n"
            "Предстоящих записей нет (или они отменены).",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="m_back")]
            ]),
        )
        return

    lines = ["📋 <b>Предстоящие записи:</b>\n"]
    for date, time, name, phone, client_id, client_username in rows:
        try:
            date_label = datetime.strptime(date, "%Y-%m-%d").strftime("%d.%m.%Y")
        except ValueError:
            date_label = date
        if client_username:
            tg_line = f"📱 Telegram: @{escape(client_username)}"
        elif client_id:
            tg_line = f"📱 Telegram: без username (id: <code>{client_id}</code>)"
        else:
            tg_line = "📱 Telegram: —"
        lines.append(
            f"• {date_label} {time} — {escape(name)} | {escape(phone)}\n"
            f"  {tg_line}"
        )

    await call.message.edit_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="m_back")]
        ])
    )


@router.callback_query(F.data == "m_back")
async def master_back(call: CallbackQuery):
    if not is_master(call.from_user.id):
        return
    await call.message.edit_text(
        "👩‍💼 <b>Панель мастера</b>\n\nВыберите действие:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📅 Добавить рабочие дни", callback_data="m_add_days")],
            [InlineKeyboardButton(text="🗑 Удалить день/слот", callback_data="m_del_slot")],
            [InlineKeyboardButton(text="❌ Отмена записи", callback_data="m_cancel")],
            [InlineKeyboardButton(text="📋 Все записи", callback_data="m_bookings")],
        ])
    )

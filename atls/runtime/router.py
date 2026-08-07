"""
Router — cổng "việc này có phải của mình không".

Tầng lọc RẺ, hoàn toàn deterministic, không gọi model. Nó quyết định có sinh một
lượt agent hay không. Tin không qua cổng vẫn được ghi vào archive để làm ngữ cảnh
nền — lần sau được gọi thì agent vẫn biết hai người vừa nói gì.

Vì sao cần cổng này khi model đã có luật im lặng: mỗi lượt CLI tốn vài giây và một
lượng token thật. Một group 200 tin/ngày mà tin nào cũng đánh thức agent thì vừa
chậm vừa đắt, và bot lắm lời là thứ bị tắt đầu tiên trong group người thật.

Tầng hai (luật im lặng phía model) vẫn cần, vì cổng này không hiểu ngữ cảnh: nó
không biết "@alice thôi khỏi" nghĩa là đừng trả lời.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from atls.telegram.model import Incoming


class Decision(str, Enum):
    ANSWER = "answer"       # gọi agent
    BACKGROUND = "background"  # chỉ lưu archive
    COMMAND = "command"     # lệnh hệ thống, runtime tự xử, không gọi agent
    IGNORE = "ignore"       # không lưu, không xử (tin của chính bot, tin rỗng)


# Lệnh do runtime tự xử lý, KHÔNG đánh thức agent. Chúng phải trả lời tức thì —
# bắt người dùng chờ 8 giây để nghe "đã reset" là vô lý.
SYSTEM_COMMANDS = frozenset({
    "start", "help", "status", "reset", "stop", "done", "nhớ", "recall", "whoami",
})

# Dấu hiệu việc sẽ chạy lâu → gửi ack NGAY thay vì đợi hết 12 giây.
# Cố ý thiên về bỏ sót: đoán nhầm "lâu" cho một câu hỏi ngắn thì người dùng nhận
# hai tin nhắn cho một câu hỏi — phiền hơn là chờ.
_LONG_HINTS = re.compile(
    r"(?i)\b(chạy|check|kiểm tra|kiểm lại|tra|tìm|search|build|deploy|test|"
    r"phân tích|analyze|sửa|fix|refactor|viết|tạo|generate|export|import|"
    r"đồng bộ|sync|migrate|crawl|scan|report|báo cáo|tổng hợp|so sánh)\b"
)


@dataclass(frozen=True)
class Route:
    decision: Decision
    reason: str
    addressed: bool = False
    command: str = ""
    args: str = ""
    likely_long: bool = False


def classify(msg: Incoming, bot_username: str, triggers: tuple[str, ...] = ()) -> Route:
    if msg.sender_is_bot:
        # Kể cả bot khác: hai bot trả lời nhau là một vòng lặp vô tận có thật.
        return Route(Decision.IGNORE, "tin của bot")

    if not msg.text and not msg.media:
        return Route(Decision.IGNORE, "tin rỗng")

    # Tin đã sửa: vào archive, KHÔNG đánh thức agent.
    #
    # Telegram cấp `update_id` mới cho mỗi lần sửa, nên nếu để nó đi tiếp thì một người
    # sửa lỗi chính tả sẽ nhận câu trả lời thứ hai cho cùng một câu hỏi. Cái giá là
    # người ta sửa tin để BỔ SUNG "@alice" thì ta không thấy — đổi lại đúng, vì gõ một
    # tin mới là chuyện một giây, còn trả lời hai lần thì không rút lại được.
    if msg.edited:
        return Route(Decision.BACKGROUND, "tin đã sửa")

    answered = _answer_route(msg, bot_username, triggers)

    if msg.is_command:
        cmd, args = msg.command()
        if not msg.command_is_for(bot_username):
            # `/poll@bot_khac` — người ta đang nói với bot khác trong cùng phòng.
            return Route(Decision.BACKGROUND, f"lệnh /{cmd} gửi cho @{msg.command_target()}")
        if cmd in SYSTEM_COMMANDS:
            return Route(Decision.COMMAND, f"/{cmd}", addressed=True, command=cmd, args=args)
        # Lệnh lạ trong chat riêng, hoặc lệnh ghi rõ `@bot_ta`, hoặc kèm @mention/reply:
        # rõ ràng là gọi ta → để agent tự hiểu người dùng muốn gì.
        if msg.chat_kind == "private" or msg.command_target() or answered:
            return Route(Decision.ANSWER, f"lệnh /{cmd}", addressed=True,
                         likely_long=_likely_long(msg.text))
        # Lệnh lạ, không đích, trong group: KHÔNG đánh thức agent. Một group thật có
        # nhiều bot và `/deploy` rất có thể là của bot khác — đánh thức nhầm thì tốn
        # một lượt CLI thật và một câu trả lời chen ngang.
        return Route(Decision.BACKGROUND, f"lệnh /{cmd} không ghi đích trong group")

    return answered or Route(Decision.BACKGROUND, "không gọi thẳng agent")


def _answer_route(msg: Incoming, bot_username: str, triggers: tuple[str, ...]) -> Route | None:
    """Tin này có gọi thẳng ta không, bỏ qua chuyện nó có phải lệnh hay không."""
    # Chat riêng: mọi tin đều dành cho agent. Không có ai khác trong phòng để nói cùng.
    if msg.chat_kind == "private":
        return Route(Decision.ANSWER, "chat riêng", addressed=True,
                     likely_long=_likely_long(msg.text))

    if msg.mentions(bot_username):
        return Route(Decision.ANSWER, "được @mention", addressed=True,
                     likely_long=_likely_long(msg.text))

    if msg.replies_to(bot_username):
        return Route(Decision.ANSWER, "reply vào tin của agent", addressed=True,
                     likely_long=_likely_long(msg.text))

    low = msg.text.lower()
    for trigger in triggers:
        if trigger and trigger in low:
            return Route(Decision.ANSWER, f"trigger '{trigger}'", addressed=True,
                         likely_long=_likely_long(msg.text))
    return None


def _likely_long(text: str) -> bool:
    if len(text) > 400:
        return True   # đề bài dài thường kèm việc dài
    return bool(_LONG_HINTS.search(text))


def merge(routes: list[Route]) -> Route:
    """Gộp quyết định cho một CHÙM tin đã debounce.

    Chỉ cần MỘT tin trong chùm gọi thẳng agent là cả chùm được trả lời — người ta
    hay gõ "@alice" rồi mới gõ nội dung ở tin sau, và trả lời riêng tin đầu là trả
    lời khi chưa đọc câu hỏi.
    """
    answering = [r for r in routes if r.decision is Decision.ANSWER]
    if not answering:
        return routes[0] if routes else Route(Decision.IGNORE, "chùm rỗng")
    return Route(
        Decision.ANSWER,
        answering[0].reason,
        addressed=True,
        likely_long=any(r.likely_long for r in answering),
    )

"""
Khả năng mở rộng — thứ agent gọi được, không phải thứ runtime tự làm.

Ranh giới: runtime biết **gửi** một file PDF lên Telegram; nó không biết và không
được biết cách lái trình duyệt để tạo ảnh. Việc đó là một *skill* của agent
(`skills/atls-image-gen/`), vì nó cần suy luận, cần xử lý giao diện đổi, cần biết
lúc nào phải nhờ người thật đăng nhập.

Nhét kỹ năng đó vào runtime là cách nhanh nhất để có một hàm 400 dòng vỡ mỗi lần
ChatGPT đổi layout.
"""

from atls.capabilities.pdf import markdown_to_pdf
from atls.capabilities.handoff import request_human

__all__ = ["markdown_to_pdf", "request_human"]

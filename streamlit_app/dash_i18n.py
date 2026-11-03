"""UI strings (Vietnamese default, English) + plain-language names for technical codes."""
from __future__ import annotations

LANGS = {"vi": "Tiếng Việt", "en": "English"}

TEXT: dict[str, dict[str, str]] = {
    "en": {
        "brand": "Omnichannel Data", "brand_sub": "E-commerce data platform",
        "nav_overview": "Overview", "nav_sales": "Sales", "nav_recon": "Money matching", "nav_pipeline": "Data pipeline",
        "nav_guide": "How to read this", "nav_settings": "Settings", "support": "Support", "language": "Language",
        "download": "Download", "download_report": "Download findings (CSV)",
        "timeframe": "Timeframe", "channel": "Channel", "view_by": "View by", "all_time": "All time", "all": "All",
        "last_7": "Last 7 days", "last_14": "Last 14 days", "last_30": "Last 30 days",
        "day": "Day", "week": "Week", "month": "Month",
        "title_overview": "Overview", "title_sales": "Sales", "title_recon": "Money matching", "title_pipeline": "Data pipeline",
        "title_guide": "How to read this dashboard",
        "sim_note": "All data is simulated: marketplace, order-system, POS and finance simulators with deliberately planted errors. It is not real business data.",
        "story_title": "The short story",
        "story_what": "What happened", "story_why": "Why it matters", "story_todo": "What to do next",
        "kpi_net": "Net sales", "kpi_orders": "Orders", "kpi_aov": "Average order value",
        "kpi_clean": "Orders with matching money", "kpi_issues": "Orders to review", "kpi_amount": "Money not yet explained",
        "kpi_of_orders": "of {n} orders", "kpi_timing_note": "{n} more are only waiting to sync",
        "activity": "Sales activity", "activity_sub": "Net sales per {unit}",
        "weakest": "Needs attention", "strongest": "Healthiest checks", "ok_suffix": "OK",
        "channel_board": "Channel leaderboard", "findings_board": "Biggest open findings",
        "net_sales_cancel": "Net sales {sales} · cancel rate {rate}", "order_label": "Order",
        "sales_by_channel": "Gross sales vs refunds by channel", "gross": "Gross sales", "refunds": "Refunds",
        "daily_detail": "Daily detail",
        "recon_intro": "Every order is compared across four systems: the marketplace, the order system (OMS), payments and the settlement paid out by the marketplace. A mismatch is a *finding*.",
        "findings_by_check": "Findings by check", "findings_by_type": "What kind of problems",
        "findings_table": "Findings", "filter_class": "Status", "filter_check": "Check",
        "confirmed": "Confirmed", "timing": "Just waiting", "class_help": "**Confirmed** = a real mismatch. **Just waiting** = the other system has not caught up yet (inside the normal delay); it usually fixes itself.",
        "col_order": "Order", "col_channel": "Channel", "col_check": "Check", "col_finding": "Finding", "col_status": "Status",
        "col_amount": "Amount difference", "col_created": "Created",
        "pipe_intro": "How fresh and complete is the data behind these numbers? Each row is one table of raw data loaded from a source system.",
        "pipe_tables": "Data tables", "pipe_rows": "Rows loaded", "pipe_versions": "Kept as history", "pipe_last": "Last load (UTC)",
        "pipe_rows_chart": "Rows loaded per table", "col_table": "Table", "col_rows": "Rows", "col_batches": "Batches",
        "col_last": "Last loaded", "col_age": "Minutes ago",
        "pipe_note": "Retries, failures and recovery times are stored in the operational audit table (pipeline_run_audit).",
        "load_error": "Could not load the reporting views",
        "load_help": "Run sql/bigquery_dashboard_views.sql in BigQuery first, then `gcloud auth application-default login` and set GCP_PROJECT_ID. For an offline demo set DASH_DUCKDB_PATH.",
        "guide_what": "What is this?",
        "guide_what_body": "A data platform that collects orders, payments, refunds and payouts from several (simulated) sales systems into one place, checks that the money adds up, and watches that the data is fresh. This dashboard is the window into it.",
        "guide_flow": "Where the data comes from", "guide_glossary": "Words you will see",
        "flow_1": "Source systems", "flow_1d": "Marketplace · order system · POS · finance",
        "flow_2": "Ingestion", "flow_2d": "Safe, repeatable loading with retries",
        "flow_3": "Warehouse", "flow_3d": "BigQuery + dbt models",
        "flow_4": "Checks", "flow_4d": "Money matching + data quality + alerts",
        "flow_5": "This dashboard", "flow_5d": "Numbers anyone can read",
        "g_finding": "Finding", "g_finding_d": "A place where two systems disagree (for example, an order has no payment).",
        "g_confirmed": "Confirmed", "g_confirmed_d": "The disagreement is older than the normal delay, so it is a real problem to investigate.",
        "g_timing": "Just waiting", "g_timing_d": "The disagreement is recent; the other system probably has not caught up yet.",
        "g_settle": "Settlement", "g_settle_d": "The money the marketplace actually pays out to the seller after fees, refunds and discounts.",
        "g_oms": "OMS", "g_oms_d": "The internal order management system that tracks fulfilment.",
        "g_clean": "Orders with matching money", "g_clean_d": "Share of orders with no confirmed finding.",
        "g_fresh": "Freshness", "g_fresh_d": "How long ago the data was last loaded.",
        "headline_ok": "{clean} of orders have matching money; {issues} orders need a closer look.",
        "story_what_body": "We compared {checked} orders across the marketplace, order system, payments and payouts. {issues} orders have a confirmed problem; {timing} more are only waiting for another system to catch up.",
        "story_why_body": "About {amount} is not yet explained. The weakest area is **{weak}** ({weak_pct} of its checks pass).",
        "story_todo_body": "Start with **{top}** ({n} orders). Open *Money matching* to see the exact orders; recent differences resolve on their own.",
        "story_all_good": "Nothing to fix right now: every confirmed check passes.",
        "trend_note": "vs previous 7 days", "unit_orders": "orders",
    },
    "vi": {
        "brand": "Omnichannel Data", "brand_sub": "Nền tảng dữ liệu thương mại điện tử",
        "nav_overview": "Tổng quan", "nav_sales": "Doanh thu", "nav_recon": "Đối chiếu tiền", "nav_pipeline": "Luồng dữ liệu",
        "nav_guide": "Cách đọc dashboard", "nav_settings": "Cài đặt", "support": "Hỗ trợ", "language": "Ngôn ngữ",
        "download": "Tải xuống", "download_report": "Tải danh sách lỗi (CSV)",
        "timeframe": "Thời gian", "channel": "Kênh bán", "view_by": "Xem theo", "all_time": "Toàn bộ", "all": "Tất cả",
        "last_7": "7 ngày gần nhất", "last_14": "14 ngày gần nhất", "last_30": "30 ngày gần nhất",
        "day": "Ngày", "week": "Tuần", "month": "Tháng",
        "title_overview": "Tổng quan", "title_sales": "Doanh thu", "title_recon": "Đối chiếu tiền", "title_pipeline": "Luồng dữ liệu",
        "title_guide": "Cách đọc dashboard này",
        "sim_note": "Toàn bộ dữ liệu là mô phỏng: hệ thống sàn, quản lý đơn, POS và tài chính giả lập, có cài sẵn lỗi để kiểm thử. Đây không phải dữ liệu kinh doanh thật.",
        "story_title": "Câu chuyện ngắn",
        "story_what": "Chuyện gì đã xảy ra", "story_why": "Vì sao quan trọng", "story_todo": "Nên làm gì tiếp",
        "kpi_net": "Doanh thu thuần", "kpi_orders": "Số đơn hàng", "kpi_aov": "Giá trị đơn trung bình",
        "kpi_clean": "Đơn khớp tiền", "kpi_issues": "Đơn cần kiểm tra", "kpi_amount": "Tiền chưa khớp",
        "kpi_of_orders": "trên {n} đơn", "kpi_timing_note": "còn {n} đơn chỉ đang chờ đồng bộ",
        "activity": "Hoạt động bán hàng", "activity_sub": "Doanh thu thuần theo {unit}",
        "weakest": "Cần chú ý nhất", "strongest": "Ổn định nhất", "ok_suffix": "đạt",
        "channel_board": "Xếp hạng kênh bán", "findings_board": "Lỗi lớn nhất đang mở",
        "net_sales_cancel": "Doanh thu thuần {sales} · tỷ lệ hủy {rate}", "order_label": "Đơn",
        "sales_by_channel": "Doanh thu gộp và hoàn tiền theo kênh", "gross": "Doanh thu gộp", "refunds": "Hoàn tiền",
        "daily_detail": "Chi tiết theo ngày",
        "recon_intro": "Mỗi đơn hàng được so sánh giữa bốn hệ thống: sàn bán hàng, hệ thống quản lý đơn (OMS), thanh toán và tiền sàn quyết toán cho người bán. Chỗ nào lệch gọi là một *phát hiện*.",
        "findings_by_check": "Phát hiện theo loại kiểm tra", "findings_by_type": "Là loại vấn đề gì",
        "findings_table": "Danh sách phát hiện", "filter_class": "Trạng thái", "filter_check": "Loại kiểm tra",
        "confirmed": "Đã xác nhận", "timing": "Chỉ đang chờ", "class_help": "**Đã xác nhận** = lệch thật. **Chỉ đang chờ** = hệ thống kia chưa kịp cập nhật (trong độ trễ bình thường), thường tự hết.",
        "col_order": "Đơn", "col_channel": "Kênh", "col_check": "Loại kiểm tra", "col_finding": "Phát hiện", "col_status": "Trạng thái",
        "col_amount": "Chênh lệch", "col_created": "Ngày tạo",
        "pipe_intro": "Dữ liệu phía sau các con số mới và đầy đủ đến đâu? Mỗi dòng là một bảng dữ liệu thô nạp từ một hệ thống nguồn.",
        "pipe_tables": "Bảng dữ liệu", "pipe_rows": "Số dòng đã nạp", "pipe_versions": "Phiên bản lưu lịch sử", "pipe_last": "Lần nạp cuối (UTC)",
        "pipe_rows_chart": "Số dòng nạp theo bảng", "col_table": "Bảng", "col_rows": "Số dòng", "col_batches": "Số lần nạp",
        "col_last": "Nạp lần cuối", "col_age": "Phút trước",
        "pipe_note": "Số lần thử lại, lỗi và thời gian phục hồi nằm trong bảng audit vận hành (pipeline_run_audit).",
        "load_error": "Không tải được các view báo cáo",
        "load_help": "Chạy sql/bigquery_dashboard_views.sql trong BigQuery trước, sau đó `gcloud auth application-default login` và đặt GCP_PROJECT_ID. Muốn chạy thử offline thì đặt DASH_DUCKDB_PATH.",
        "guide_what": "Đây là gì?",
        "guide_what_body": "Một nền tảng dữ liệu gom đơn hàng, thanh toán, hoàn tiền và tiền quyết toán từ nhiều hệ thống bán hàng (mô phỏng) về một chỗ, kiểm tra xem tiền có khớp không, và theo dõi dữ liệu có còn mới không. Dashboard này là cửa sổ để nhìn vào đó.",
        "guide_flow": "Dữ liệu đi từ đâu đến đâu", "guide_glossary": "Các từ bạn sẽ gặp",
        "flow_1": "Hệ thống nguồn", "flow_1d": "Sàn · quản lý đơn · POS · tài chính",
        "flow_2": "Nạp dữ liệu", "flow_2d": "Nạp an toàn, lặp lại được, tự thử lại",
        "flow_3": "Kho dữ liệu", "flow_3d": "BigQuery + mô hình dbt",
        "flow_4": "Kiểm tra", "flow_4d": "Đối chiếu tiền + chất lượng dữ liệu + cảnh báo",
        "flow_5": "Dashboard này", "flow_5d": "Con số ai cũng đọc được",
        "g_finding": "Phát hiện", "g_finding_d": "Chỗ hai hệ thống không khớp nhau (ví dụ đơn hàng chưa có thanh toán).",
        "g_confirmed": "Đã xác nhận", "g_confirmed_d": "Chỗ lệch đã quá độ trễ bình thường, tức là vấn đề thật cần điều tra.",
        "g_timing": "Chỉ đang chờ", "g_timing_d": "Chỗ lệch còn mới, nhiều khả năng hệ thống kia chưa kịp cập nhật.",
        "g_settle": "Quyết toán", "g_settle_d": "Số tiền thực tế sàn trả cho người bán sau khi trừ phí, hoàn tiền và giảm giá.",
        "g_oms": "OMS", "g_oms_d": "Hệ thống quản lý đơn hàng nội bộ, theo dõi việc giao hàng.",
        "g_clean": "Đơn khớp tiền", "g_clean_d": "Tỷ lệ đơn không có phát hiện nào đã xác nhận.",
        "g_fresh": "Độ mới", "g_fresh_d": "Dữ liệu được nạp lần cuối cách đây bao lâu.",
        "headline_ok": "{clean} đơn hàng khớp tiền; {issues} đơn cần xem kỹ hơn.",
        "story_what_body": "Hệ thống đã đối chiếu {checked} đơn giữa sàn, hệ thống quản lý đơn, thanh toán và tiền quyết toán. {issues} đơn có vấn đề đã xác nhận; {timing} đơn khác chỉ đang chờ hệ thống kia cập nhật.",
        "story_why_body": "Khoảng {amount} chưa được giải thích. Mảng yếu nhất là **{weak}** (chỉ {weak_pct} lượt kiểm tra đạt).",
        "story_todo_body": "Bắt đầu từ **{top}** ({n} đơn). Mở trang *Đối chiếu tiền* để xem đúng các đơn; những chỗ lệch mới sẽ tự hết.",
        "story_all_good": "Hiện không có gì cần sửa: mọi kiểm tra đã xác nhận đều đạt.",
        "trend_note": "so với 7 ngày trước", "unit_orders": "đơn",
    },
}

CHECK_NAMES = {
    "en": {"Order vs OMS": "Order vs order system", "Payment": "Payments", "Currency": "Currency", "Refund": "Refunds",
           "Settlement": "Marketplace payout"},
    "vi": {"Order vs OMS": "Đơn so với hệ thống quản lý đơn", "Payment": "Thanh toán", "Currency": "Loại tiền",
           "Refund": "Hoàn tiền", "Settlement": "Tiền sàn quyết toán"},
}

FINDING_NAMES = {
    "en": {"AMOUNT_MISMATCH": "Amount differs", "MISSING": "Missing", "MISSING_IN_OMS": "Not in order system",
           "CURRENCY_MISMATCH": "Different currency", "REFUND_WITHOUT_PAYMENT": "Refund with no payment",
           "REFUND_EXCEEDS_PAID": "Refund larger than payment", "FORMULA_MISMATCH": "Payout does not add up",
           "GROSS_MISMATCH": "Gross differs from order", "PENDING_SYNC": "Waiting to sync",
           "PENDING_SETTLEMENT": "Payout not due yet"},
    "vi": {"AMOUNT_MISMATCH": "Sai số tiền", "MISSING": "Thiếu", "MISSING_IN_OMS": "Không có trong hệ thống đơn",
           "CURRENCY_MISMATCH": "Khác loại tiền", "REFUND_WITHOUT_PAYMENT": "Hoàn tiền nhưng chưa có thanh toán",
           "REFUND_EXCEEDS_PAID": "Hoàn nhiều hơn số đã thu", "FORMULA_MISMATCH": "Tiền quyết toán không khớp công thức",
           "GROSS_MISMATCH": "Doanh thu gộp lệch với đơn", "PENDING_SYNC": "Đang chờ đồng bộ",
           "PENDING_SETTLEMENT": "Chưa đến hạn quyết toán"},
}


def make_tr(lang: str):
    table = TEXT.get(lang, TEXT["vi"])

    def tr(key: str, **kw) -> str:
        s = table.get(key) or TEXT["en"].get(key, key)
        return s.format(**kw) if kw else s
    return tr


def check_name(lang: str, check_type: str) -> str:
    return CHECK_NAMES.get(lang, CHECK_NAMES["vi"]).get(check_type, check_type)


def finding_name(lang: str, code: str) -> str:
    return FINDING_NAMES.get(lang, FINDING_NAMES["vi"]).get(code, code.replace("_", " ").title())

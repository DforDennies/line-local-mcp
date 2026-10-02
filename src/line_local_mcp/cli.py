from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .bootstrap import BootstrapError, setup_key
from .database import LineDatabaseError
from .repository import LineRepository, _load_whitelist
from .server import create_server

WHITELIST_PATH = Path(__file__).resolve().parent.parent.parent / "chat_whitelist.json"


def _print_json(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _format_time(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return iso or ""


def _print_chats(chats: list[dict], total: int | None = None):
    if not chats:
        print("（沒有符合的聊天室）")
        return
    for c in chats:
        updated = _format_time(c.get("updated_at"))
        unread = c.get("unread_count", 0)
        badge = f"  [{unread} 未讀]" if unread else ""
        print(f"  {c['chat_id']}  {c['name']}  ({c['type']}){badge}  {updated}")
    shown = len(chats)
    if total and total > shown:
        print(f"\n  顯示 {shown}/{total}，用 --limit 調整")


def _print_messages(messages: list[dict]):
    if not messages:
        print("（沒有訊息）")
        return
    for m in messages:
        time_str = _format_time(m.get("sent_at"))
        sender = m.get("sender_name", "?")
        text = m.get("text", "")
        redacted = " [已遮蔽]" if m.get("redacted") else ""
        print(f"  [{time_str}] {sender}: {text}{redacted}")


# -- subcommand handlers --

def cmd_serve(args):
    server = create_server()
    if args.transport == "stdio":
        server.run(transport="stdio")
    else:
        server.run(
            transport="streamable-http",
            host="127.0.0.1",
            port=args.port,
            streamable_http_path="/mcp",
            json_response=True,
            stateless_http=True,
        )


def cmd_doctor(args):
    try:
        result = LineRepository().status()
    except LineDatabaseError as exc:
        print(f"LINE MCP is not ready: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    if args.json:
        _print_json(result)
    else:
        ok = "✓" if result.get("connected") else "✗"
        print(f"  狀態:     {ok} connected")
        print(f"  聊天室:   {result.get('chat_count', '?')}")
        print(f"  訊息數:   {result.get('message_count', '?')}")
        print(f"  最新訊息: {_format_time(result.get('newest_message_at'))}")
        print(f"  DB 修改:  {_format_time(result.get('database_modified_at'))}")
        print(f"  遮蔽模式: {'開' if result.get('sensitive_text_redaction') else '關'}")


def cmd_setup_key(args):
    try:
        setup_key()
    except (BootstrapError, LineDatabaseError) as exc:
        print(f"LINE MCP key setup failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


def cmd_chats(args):
    try:
        repo = LineRepository()
        result = repo.list_chats(
            limit=args.limit,
            unread_only=args.unread,
            name_contains=args.name,
            include_official=args.official,
        )
    except LineDatabaseError as exc:
        print(f"錯誤: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    if args.json:
        _print_json(result)
    else:
        _print_chats(result["chats"], result.get("total_matched"))


def cmd_messages(args):
    try:
        repo = LineRepository()
        result = repo.get_messages(
            args.chat_id,
            limit=args.limit,
            after=args.after,
            before=args.before,
        )
    except LineDatabaseError as exc:
        print(f"錯誤: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    if args.json:
        _print_json(result)
    else:
        total = result.get("total_matched", 0)
        shown = result.get("count", 0)
        print(f"  聊天室: {args.chat_id}")
        if total > shown:
            print(f"  顯示 {shown}/{total} 則訊息\n")
        else:
            print(f"  共 {shown} 則訊息\n")
        _print_messages(result["messages"])


def cmd_search(args):
    try:
        repo = LineRepository()
        result = repo.search_messages(
            args.query,
            limit=args.limit,
            chat_id=args.chat,
            after=args.after,
            before=args.before,
            include_official=args.official,
        )
    except LineDatabaseError as exc:
        print(f"錯誤: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    if args.json:
        _print_json(result)
    else:
        total = result.get("total_matched", 0)
        shown = result.get("count", 0)
        print(f"  搜尋: \"{args.query}\"")
        if total > shown:
            print(f"  顯示 {shown}/{total} 則\n")
        else:
            print(f"  共 {shown} 則\n")
        _print_messages(result["messages"])


def cmd_recent(args):
    try:
        repo = LineRepository()
        result = repo.recent_activity(
            hours=args.hours,
            chat_limit=args.chat_limit,
            messages_per_chat=args.per_chat,
            include_official=args.official,
        )
    except LineDatabaseError as exc:
        print(f"錯誤: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    if args.json:
        _print_json(result)
    else:
        print(f"  最近 {args.hours} 小時活動\n")
        for chat in result.get("chats", []):
            total_msg = chat.get("total_matched_messages", 0)
            print(f"  ── {chat['name']} ({chat['type']}) [{total_msg} 則] ──")
            _print_messages(chat.get("messages", []))
            print()


def cmd_whitelist_list(args):
    if not WHITELIST_PATH.is_file():
        print("  白名單檔不存在（全部聊天室皆可存取）")
        print(f"  路徑: {WHITELIST_PATH}")
        return
    with open(WHITELIST_PATH) as f:
        data = json.load(f)
    chats = data.get("allowed_chats", [])
    if not chats:
        print("  白名單為空（沒有任何聊天室可存取）")
        return
    print(f"  白名單: {len(chats)} 個聊天室\n")
    for c in chats:
        print(f"  {c['id']}  {c.get('name', '(無名稱)')}")


def cmd_whitelist_find(args):
    try:
        original = _load_whitelist
        from line_local_mcp import repository
        repository._load_whitelist = lambda: None
        repo = LineRepository()
        result = repo.list_chats(
            name_contains=args.keyword,
            limit=args.limit,
            include_official=args.official,
        )
        repository._load_whitelist = original
    except LineDatabaseError as exc:
        print(f"錯誤: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    if args.json:
        _print_json(result)
    else:
        print(f"  搜尋: \"{args.keyword}\"（繞過白名單）\n")
        _print_chats(result["chats"], result.get("total_matched"))
        print(f"\n  用 `whitelist add <chat_id>` 加入白名單")


def cmd_whitelist_add(args):
    if WHITELIST_PATH.is_file():
        with open(WHITELIST_PATH) as f:
            data = json.load(f)
    else:
        data = {"allowed_chats": []}

    chats = data.get("allowed_chats", [])
    if any(c["id"] == args.chat_id for c in chats):
        print(f"  已存在: {args.chat_id}")
        return

    name = args.name or args.chat_id
    chats.append({"id": args.chat_id, "name": name})
    data["allowed_chats"] = chats

    with open(WHITELIST_PATH, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"  已加入: {name} ({args.chat_id})")
    print("  重啟 MCP server 後生效")


def cmd_whitelist_remove(args):
    if not WHITELIST_PATH.is_file():
        print("  白名單檔不存在")
        return
    with open(WHITELIST_PATH) as f:
        data = json.load(f)

    chats = data.get("allowed_chats", [])
    before_count = len(chats)
    chats = [c for c in chats if c["id"] != args.chat_id]

    if len(chats) == before_count:
        print(f"  找不到: {args.chat_id}")
        return

    data["allowed_chats"] = chats
    with open(WHITELIST_PATH, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"  已移除: {args.chat_id}")
    print("  重啟 MCP server 後生效")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="line-local-mcp",
        description="讀取 LINE Desktop 本地聊天記錄（唯讀）",
    )
    sub = parser.add_subparsers(dest="command")

    # -- serve (MCP server) --
    p_serve = sub.add_parser("serve", help="啟動 MCP server")
    p_serve.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    p_serve.add_argument("--port", type=int, default=8765)

    # -- doctor --
    p_doctor = sub.add_parser("doctor", help="檢查 LINE DB 連線狀態")
    p_doctor.add_argument("--json", action="store_true", help="JSON 輸出")

    # -- setup-key --
    sub.add_parser("setup-key", help="首次提取加密 key（存入 Keychain）")

    # -- chats --
    p_chats = sub.add_parser("chats", help="列出聊天室")
    p_chats.add_argument("--name", help="名稱包含（不分大小寫）")
    p_chats.add_argument("--limit", type=int, default=20)
    p_chats.add_argument("--unread", action="store_true", help="只顯示未讀")
    p_chats.add_argument("--official", action="store_true", help="包含官方帳號")
    p_chats.add_argument("--json", action="store_true")

    # -- messages --
    p_msg = sub.add_parser("messages", help="讀取聊天室訊息")
    p_msg.add_argument("chat_id", help="聊天室 ID（從 chats 取得）")
    p_msg.add_argument("--limit", type=int, default=50)
    p_msg.add_argument("--after", help="起始時間 (ISO 8601)")
    p_msg.add_argument("--before", help="結束時間 (ISO 8601)")
    p_msg.add_argument("--json", action="store_true")

    # -- search --
    p_search = sub.add_parser("search", help="全文搜尋訊息")
    p_search.add_argument("query", help="搜尋關鍵字")
    p_search.add_argument("--chat", help="限定聊天室 ID")
    p_search.add_argument("--limit", type=int, default=50)
    p_search.add_argument("--after", help="起始時間 (ISO 8601)")
    p_search.add_argument("--before", help="結束時間 (ISO 8601)")
    p_search.add_argument("--official", action="store_true", help="包含官方帳號")
    p_search.add_argument("--json", action="store_true")

    # -- recent --
    p_recent = sub.add_parser("recent", help="最近活動概覽")
    p_recent.add_argument("--hours", type=int, default=24)
    p_recent.add_argument("--chat-limit", type=int, default=30)
    p_recent.add_argument("--per-chat", type=int, default=20, help="每個聊天室最多幾則")
    p_recent.add_argument("--official", action="store_true")
    p_recent.add_argument("--json", action="store_true")

    # -- whitelist --
    p_wl = sub.add_parser("whitelist", help="管理聊天室白名單")
    wl_sub = p_wl.add_subparsers(dest="wl_command")

    wl_sub.add_parser("list", help="顯示目前白名單")

    p_wl_find = wl_sub.add_parser("find", help="搜尋聊天室（繞過白名單）")
    p_wl_find.add_argument("keyword", help="名稱關鍵字")
    p_wl_find.add_argument("--limit", type=int, default=20)
    p_wl_find.add_argument("--official", action="store_true")
    p_wl_find.add_argument("--json", action="store_true")

    p_wl_add = wl_sub.add_parser("add", help="加入白名單")
    p_wl_add.add_argument("chat_id", help="聊天室 ID")
    p_wl_add.add_argument("--name", help="備註名稱")

    p_wl_rm = wl_sub.add_parser("remove", help="從白名單移除")
    p_wl_rm.add_argument("chat_id", help="聊天室 ID")

    # -- legacy flags (backward compat) --
    parser.add_argument("--setup-key", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--doctor", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default=None, help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=8765, help=argparse.SUPPRESS)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # legacy flag compat
    if args.setup_key:
        cmd_setup_key(args)
        return
    if args.doctor:
        args.json = False
        cmd_doctor(args)
        return
    if args.command is None and args.transport:
        cmd_serve(args)
        return

    handlers = {
        "serve": cmd_serve,
        "doctor": cmd_doctor,
        "setup-key": cmd_setup_key,
        "chats": cmd_chats,
        "messages": cmd_messages,
        "search": cmd_search,
        "recent": cmd_recent,
    }

    if args.command in handlers:
        handlers[args.command](args)
        return

    if args.command == "whitelist":
        wl_handlers = {
            "list": cmd_whitelist_list,
            "find": cmd_whitelist_find,
            "add": cmd_whitelist_add,
            "remove": cmd_whitelist_remove,
        }
        if args.wl_command in wl_handlers:
            wl_handlers[args.wl_command](args)
            return
        # no wl subcommand → show list
        cmd_whitelist_list(args)
        return

    # no command at all → show help
    parser.print_help()


if __name__ == "__main__":
    main()

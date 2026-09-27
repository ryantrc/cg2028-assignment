"""Local Bot API setup checks. Credentials come only from environment variables."""
import argparse
import json
import os
import urllib.parse
import urllib.request


def call(token, method, fields=None):
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(fields or {}).encode("utf-8")
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10) as reply:
        result = json.load(reply)
    if not result.get("ok"):
        raise RuntimeError(f"Telegram rejected {method}")
    return result["result"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["validate", "find-chat", "send-test"])
    args = parser.parse_args()
    token = os.environ.get("CG2028_BOT_TOKEN", "")
    if not token:
        parser.error("Set CG2028_BOT_TOKEN in the local environment")
    try:
        if args.action == "validate":
            bot = call(token, "getMe")
            print("Bot valid:", bot.get("username", "(unnamed)"))
        elif args.action == "find-chat":
            updates = call(token, "getUpdates", {"timeout": 0, "limit": 100})
            chats = set()
            for update in updates:
                for kind in ("message", "my_chat_member", "channel_post"):
                    chat = (update.get(kind) or {}).get("chat") or {}
                    if "id" in chat:
                        chats.add(str(chat["id"]))
            print("Chat IDs after recipient starts the bot:", ", ".join(sorted(chats)) or "none")
        else:
            chat = os.environ.get("CG2028_CHAT_ID", "")
            if not chat:
                parser.error("Set CG2028_CHAT_ID in the local environment")
            call(token, "sendMessage", {"chat_id": chat, "text": "CG2028 setup test message."})
            print("Test message accepted by Telegram")
    except Exception as error:
        # urllib exceptions can contain the token-bearing URL: do not print them.
        raise SystemExit(f"{args.action} failed; check token, chat ID, network, and bot access") from None


if __name__ == "__main__":
    main()

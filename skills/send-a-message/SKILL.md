---
name: send-a-message
description: Write and send a message or email in Messages, Mail, WhatsApp Web, Slack or similar apps, with the user's approval before anything is sent. Use when the user asks to text, message, email or reply to someone.
license: MIT
metadata:
  author: forge
  version: "1.0"
---

# Sending a message

1. Open the app (`open_app` "Messages" / "Mail", or `open_url` for web apps).
2. Find the conversation or start a new one:
   - Messages: `cmd+n`, then type the contact's name into the "To" field and press Enter.
   - Mail: `cmd+n`, fill "To", then "Subject", then the body.
3. Type the message exactly as the user said it into the message field. Do not add a signature or emojis.
4. Send last. Pressing Enter or clicking Send is checked by the policy gate, which asks the user to confirm.
   If they say no, stop and call `done` saying the draft is ready but not sent.
5. If the contact cannot be found, do not guess. Call `done` and ask who they meant.

---
name: system-settings
description: Change a macOS setting such as dark mode, Wi-Fi, Bluetooth, display brightness, notifications or Do Not Disturb. Use when the user wants to turn something on or off or adjust a preference in System Settings.
license: MIT
metadata:
  author: forge
  version: "1.0"
---

# Changing a macOS setting

1. Open the pane directly with `open_url` and a settings link, which is faster and more reliable than clicking:
   - Appearance (dark mode): `x-apple.systempreferences:com.apple.Appearance-Settings.extension`
   - Wi-Fi: `x-apple.systempreferences:com.apple.wifi-settings-extension`
   - Bluetooth: `x-apple.systempreferences:com.apple.BluetoothSettings`
   - Displays: `x-apple.systempreferences:com.apple.Displays-Settings.extension`
   - Notifications: `x-apple.systempreferences:com.apple.Notifications-Settings.extension`
   If `open_url` refuses a non-web link, call `open_app` "System Settings" and use its search field instead.
2. Find the switch, checkbox or radio button in the element list by its label and `click_element` it.
3. Check the next observation to confirm the value changed, then call `done`.

Never change passwords, security, privacy or user-account settings.

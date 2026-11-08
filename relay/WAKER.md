# save-clear relay: waker

The relay delivers messages between sessions but cannot message itself. After its own clear, its
listener stops and nobody wakes it. You are the waker: you received `RELAY-WAKE base=<N>`.

1. Run in Bash **with `run_in_background: true`**: `{RELAY} wait-clear --base <N>`, and end the turn
   with one line ("waiting for the relay's clear"). The harness wakes you when it exits.
2. When it exits (`CLEARED` or `TIMEOUT`), read the relay id from `{RELAY} status` and `send_message`
   to it, exactly:
   `Resume from the relay summary and start the listener again (run {RELAY} rules, step 1).`
   ("no sign of starting within 20s" is normal for a cold session: count it as delivered.)
3. Check that within 90 s `{RELAY} status` shows `"listening": true`. Only then, if the relay had
   Remote Control on, call `set_remote_control` with `enabled: true` for it (turning it on before
   the relay wakes up fails). Write one line to `{STATE}/relay.log`:
   `<date time> [waker] relay woken -> <ok or failed>`.
4. Tell the user in one line only if it failed.

Touch nothing else of the relay (rules, inbox, snapshot).

# Chat/control fixture validation

Validated on 2026-07-16 with the installed Python entry points, wire fixtures,
a disposable Windows Palworld process under Wine, and the managed UE4SS hook.
Production was checked read-only only for configuration presence; it has no
Discord bot, relay, notification webhook or Matrix alternative configured, and
no external chat service was contacted.

## Covered contracts

- Guild command registration contains seven bounded slash commands.
- Gateway Identify requests only the `GUILDS` intent; Resume preserves session
  and sequence state.
- Guild, channel, administrator and moderator allowlists refuse outsiders before
  local command execution.
- Successful interactions are acknowledged as deferred ephemeral responses and
  edited with Discord mentions disabled.
- Interaction IDs persist across attempts and are never executed twice; a failed
  initial callback releases its claim for a safe retry.
- General and mutating commands have independent per-user rate limits.
- Status and roster output omit server/player IPs, Discord-style identifiers and
  credential-shaped values.
- Restart requires `RESTART PALWORLD`; moderator/admin capability boundaries are
  enforced before subprocess execution.
- Local command output retains at most 64 KiB, response output at most 1,800
  characters, and command timeout kills the entire child process group.
- Game-chat ingress refuses non-loopback listeners, missing bearer credentials,
  disabled categories and duplicate event IDs.
- Relayed messages disable Discord mentions. Chat bodies are absent from replay
  state and audit records, and a failed Discord send can be retried safely.
- The hash-pinned UE4SS component loaded in the real current Palworld process,
  started `PalWorldSelfHostRelay`, and logged successful registration of the
  `BroadcastChatMessage` game hook.
- The hook has no bearer or Discord credential and performs no network I/O. It
  converts the configured Linux tmpfs path to Wine's `Z:` view, then atomically
  writes bounded event files for the hardened host adapter.
- A real loopback HTTP drill proved adapter bearer/body forwarding, permanent
  rejection and deletion, category filtering, bounded malformed-event removal,
  and retained retry after a transient 5xx response.
- A separate exact-confirmed external-validation transaction binds both secret
  hashes and every configured boundary to its plan. Execute verifies bot,
  application, guild and relay-channel identity, requires Discord to return the
  exact seven-command set, sends one mention-suppressed nonce probe, deletes it
  in a finally-protected cleanup, then stores an identifier- and secret-free
  receipt. Plan/status make no external request, and status invalidates the
  receipt after any bound configuration or command-definition change.

## Commands and result

```text
python3 -m unittest -v tests.test_bot_control tests.test_discord_validation tests.test_game_hook_adapter
22 tests passed
```

The validation request sequence was rechecked on 2026-07-16 against Discord's
current API v10 documentation: the authenticated current-user and current-bot-
application endpoints expose the two identities; Get Channel includes its guild
boundary; bulk guild-command overwrite returns the resulting command list;
Create Message returns the created message and accepts `allowed_mentions`; and
deleting the bot's own message returns an empty success without requiring the
permission needed to delete another user's message. See Discord's official
[OAuth2](https://docs.discord.com/developers/topics/oauth2),
[application-command](https://docs.discord.com/developers/interactions/application-commands),
[channel](https://docs.discord.com/developers/resources/channel), and
[message](https://docs.discord.com/developers/resources/message) references.

See [`mod-lifecycle-lab-validation.md`](mod-lifecycle-lab-validation.md) for
the exact runtime/artifact hashes and load evidence. Executing the prepared
transaction against an explicitly authorized Discord guild remains the only
external integration proof not available in this lab.

Once an authorized guild is configured, the remaining proof command is:

```text
sudo -u palworld palworldctl discord-validation plan
sudo -u palworld palworldctl discord-validation execute \
  --expected-plan-hash <reviewed-hash> --confirm 'VALIDATE DISCORD DELIVERY'
```

# aihubmix-usage

Show an AIHubMix account's balance, lifetime usage, spend trend, available models, and
per-key quota from the terminal. It uses only the Python standard library.

## Requirements

- Python 3.9 or newer.
- An AIHubMix `sk-...` API key.

## Setup

Set the key and, optionally, the base URL:

```bash
export AIHUB_KEY="sk-..."
export AIHUB="http://20.222.22.11/v1"      # or AIHUB_DIRECT="https://aihubmix.com/v1"
```

A trailing `/v1` in the base URL is stripped. The tool reads credentials in this order:

| Purpose | Environment variable | Option |
| - | - | - |
| Base URL | `AIHUB`, `AIHUBMIX_BASE`, `AIHUB_DIRECT` | `--base` |
| API key | `AIHUB_KEY`, `AIHUBMIX_API_KEY`, `AIHUB_API_KEY` | `--key` |
| Manage Key (optional) | `AIHUB_MANAGE_KEY`, `AIHUBMIX_MANAGE_KEY`, `AIHUB_ACCESS_TOKEN` | `--manage-key` |

The tool never prints a full key; it shows a hint such as `sk-abcd…wxyz`.

## Usage

```bash
python3 aihubmix_usage.py report   [--models] [--filter TEXT] [--limit N]
python3 aihubmix_usage.py history  [--days N] [--no-record]
python3 aihubmix_usage.py models   [--filter TEXT] [--limit N]
python3 aihubmix_usage.py watch    [--interval SEC] [--count N]
```

Shared options: `--base URL`, `--key KEY`, `--manage-key TOKEN`, `--data-dir PATH`,
`--no-color`, `--json`, `--timeout SEC`, `--retries N`.

Color follows `NO_COLOR` and the TTY state. `--json` output is always plain.

### report

The default overview. Reads the balance, the quota limits, and the account, then draws a
panel, a quota bar, an account block, and any tracked history.

- `--models` appends the model list; `--filter TEXT` narrows it; `--limit N` caps it.
- `--warn-pct` (default 20) and `--crit-pct` (default 10) set the remaining-quota colors.

```bash
python3 aihubmix_usage.py report
python3 aihubmix_usage.py report --models --filter claude --limit 10
```

### history

Records a balance snapshot and draws daily spend. The billing API returns only a lifetime
total, so the tool keeps its own snapshots in
`$XDG_DATA_HOME/aihubmix-usage/history.jsonl` (default `~/.local/share/aihubmix-usage/`).

- Run it on a schedule, or leave `watch` running, to build the trend.
- `--no-record` draws the trend without adding a snapshot.
- `--days N` limits the window (default 14) and the burn-rate average.

```bash
python3 aihubmix_usage.py history
python3 aihubmix_usage.py history --days 30 --no-record
```

### models

Lists the models the key may call, from `GET /v1/models`.

```bash
python3 aihubmix_usage.py models --filter glm --limit 20
```

### watch

Polls the balance and prints the running session spend and the last poll delta. `--count 0`
(the default) runs until interrupted.

```bash
python3 aihubmix_usage.py watch --interval 30
```

## Endpoints Used

| Endpoint | Auth | Field |
| - | - | - |
| `GET /dashboard/billing/remain` | sk- key | `total_usage` = remaining USD |
| `GET /dashboard/billing/subscription` | sk- key | `hard_limit_usd` = total quota USD |
| `GET /dashboard/billing/usage` | sk- key | `total_usage` = lifetime used, in cents |
| `GET /v1/models` | sk- key | model list |
| `GET /api/status` | sk- key | `quota_per_unit` (500000), `system_name` |
| `GET /api/user/self` | Manage Key | account profile, `quota`, `used_quota` |
| `GET /api/token/` | Manage Key | per-key `used_quota`, `remain_quota` |

Notes:

- `/dashboard/billing/usage` accepts `start_date` and `end_date` but ignores them; it always
  returns the lifetime total. The `usage` value and `hard_limit_usd - remain` agree to within
  about one cent.
- `USD = quota / quota_per_unit`, where `quota_per_unit` is 500000.
- The account endpoints need the Manage Key (access token), not the `sk-` key. Without it the
  tool skips that section and lists the 401 responses under `Problems`.
- Some hosts reset connections intermittently. The client retries `--retries` times (default
  5) with backoff; raise it if a report is still incomplete.

## JSON Output

Every command supports `--json`:

- `report` and `watch` emit the report object. `watch --json` prints one object per poll.
- `history` emits `{recorded, history}`.
- `models` emits `{count, models}`.

## Files

| File | Purpose |
| - | - |
| `aihubmix_usage.py` | CLI entry point and command dispatch. |
| `ahb_api.py` | HTTP client and endpoint wrappers. |
| `ahb_report.py` | Builds and renders the report, keys, and history. |
| `ahb_history.py` | Local snapshot store and spend aggregation. |
| `ahb_render.py` | Terminal rendering primitives (color, tables, panels, bars). |

# Screenbox Test Plan

## Overview

Systematic testing of Screenbox core functionality: lifecycle, resilience, persistence, concurrency, and agent identity.

## Test Suites

---

### T1: Lifecycle -- Start/Stop (20 instances)

**Goal:** Verify reliable creation and destruction of desktops at scale with full logging.

| # | Test | Pass Criteria |
|---|------|---------------|
| T1.1 | Create 20 desktops sequentially | All 20 running, each logged with start time + allocated ports + resources |
| T1.2 | Verify each desktop responds | Screenshot from all 20 returns valid JPEG |
| T1.3 | Stop all 20 sequentially | All stopped, each logged with stop time + uptime duration |
| T1.4 | Create 20 desktops in parallel (batches of 5) | All 20 running, no port conflicts, no race conditions |
| T1.5 | Stop all 20 in parallel | All stopped cleanly |
| T1.6 | Rapid create-destroy cycle (create, screenshot, destroy x 20) | No leaked containers, no orphan ports |

**Log format per operation:**
```json
{"ts": "ISO8601", "op": "create|destroy", "desktop_id": "...", "duration_ms": N, "ports": {...}, "resources": {...}, "error": null}
```

---

### T2: Pause/Resume (Hibernate)

**Goal:** Verify Docker pause/unpause preserves desktop state.

| # | Test | Pass Criteria |
|---|------|---------------|
| T2.1 | Create desktop, navigate to page, pause | Container status = paused, no CPU usage |
| T2.2 | Resume paused desktop | Container running, page still loaded (screenshot matches) |
| T2.3 | Pause 10 desktops simultaneously | All paused, total CPU drops to ~0 |
| T2.4 | Resume 10 desktops simultaneously | All running, all pages intact |
| T2.5 | Pause, wait 5 minutes, resume | State preserved after extended pause |
| T2.6 | Idle auto-pause (idle_pause_minutes) | Desktop pauses after configured idle time |

---

### T3: Crash Recovery & Resilience

**Goal:** Verify behavior when things go wrong.

| # | Test | Pass Criteria |
|---|------|---------------|
| T3.1 | Kill container (docker kill -s 9) | MCP reports desktop as stopped, no zombie processes |
| T3.2 | Kill Chrome inside container | Entrypoint should detect and either restart Chrome or report failure |
| T3.3 | Kill Xvfb inside container | Container should stop gracefully or restart X |
| T3.4 | OOM kill (allocate memory until 512m limit hit) | Container OOM-killed, logged, profile intact on host |
| T3.5 | Docker daemon restart | Containers restart (if policy set), MCP re-syncs state |
| T3.6 | Disk full on host | Graceful error, no data corruption |
| T3.7 | Network disconnected (docker network disconnect) | Desktop isolated but still runs locally |
| T3.8 | Create desktop with same ID as crashed one | Profile restored, clean start |

---

### T4: Profile Persistence

**Goal:** Verify browser profiles survive container lifecycle.

| # | Test | Pass Criteria |
|---|------|---------------|
| T4.1 | Create desktop, navigate to site, login, destroy, recreate | Session cookies preserved, still logged in |
| T4.2 | Create desktop, add bookmark, destroy, recreate | Bookmark still present |
| T4.3 | Create desktop, download file, destroy, recreate | File in downloads dir on host |
| T4.4 | Create desktop A with profile, create desktop B with same profile_id | Both share profile? Or conflict detection? |
| T4.5 | Profile disk usage after 1hr browsing | Profile size reasonable, no runaway growth |
| T4.6 | Corrupt profile dir, recreate desktop | Chrome starts fresh (not crash loop) |

---

### T5: Concurrent Agent Access

**Goal:** Verify multiple agents can work with desktops without conflicts.

| # | Test | Pass Criteria |
|---|------|---------------|
| T5.1 | Agent A and Agent B each create own desktop | Both succeed, no interference |
| T5.2 | Agent A and Agent B send commands to SAME desktop | Commands execute in order, no corruption |
| T5.3 | Agent A clicks while Agent B types on same desktop | Both complete, no crash (may produce garbled input -- document behavior) |
| T5.4 | Agent A destroys desktop while Agent B is using it | Agent B gets clear error, no hang |
| T5.5 | 5 agents each creating 4 desktops simultaneously | All 20 created, no port/ID conflicts |
| T5.6 | Agent A pauses desktop, Agent B tries to screenshot | Clear error: "desktop is paused" |

---

### T6: Agent Identity & Authorization

**Goal:** Verify agents are identified and access is controlled.

| # | Test | Pass Criteria |
|---|------|---------------|
| T6.1 | Check if agent ID is tracked per operation | Logs show which agent performed each action |
| T6.2 | Agent header (X-Agent-Id or similar) in MCP calls | Agent identity propagated to logs |
| T6.3 | Desktop ownership -- agent can only destroy own desktops? | Policy enforced or documented as shared |
| T6.4 | Audit trail -- who created, who last used each desktop | Queryable from API or logs |
| T6.5 | Rate limiting per agent | No single agent can exhaust all resources |
| T6.6 | Max desktops per agent (quota) | Configurable limit per agent identity |

**Current status:** Agent identity is NOT implemented yet. This suite defines what needs to be built.

---

### T7: Resource Limits & Quotas

**Goal:** Verify resource controls work correctly.

| # | Test | Pass Criteria |
|---|------|---------------|
| T7.1 | Memory limit enforced (512m default) | Container OOM-killed at limit, not host |
| T7.2 | CPU limit (if configured) | Container throttled, not starving host |
| T7.3 | Disk quota per desktop | Warning at 80%, hard limit at quota |
| T7.4 | Max desktops limit | Error returned when limit reached, clear message |
| T7.5 | Port range exhaustion | Clear error when no ports available |
| T7.6 | Total system resource check before create | Refuse if host RAM/disk too low |

---

### T8: Logging & Observability

**Goal:** Verify all operations are logged with enough detail for debugging.

| # | Test | Pass Criteria |
|---|------|---------------|
| T8.1 | Create desktop -- log entry with timestamps, ports, config | Present in logs/{id}.jsonl |
| T8.2 | Every MCP tool call logged | Action, args, duration, result/error |
| T8.3 | Container events logged (start, stop, OOM, crash) | Captured from Docker events or healthcheck |
| T8.4 | Screenshot logging (optional, config flag) | When enabled, screenshots saved to logs/screenshots/ |
| T8.5 | Log rotation / max size | Logs don't grow unbounded |
| T8.6 | Dashboard shows log summary per desktop | Last N events visible in UI |

---

### T9: Dashboard Reliability

**Goal:** Verify dashboard stays functional under load.

| # | Test | Pass Criteria |
|---|------|---------------|
| T9.1 | Dashboard with 20 desktops | All tiles visible, thumbnails update |
| T9.2 | Desktop created/destroyed while dashboard open | Dashboard updates automatically |
| T9.3 | Dashboard with 0 desktops | Empty state shown correctly |
| T9.4 | Screenshot API under load (20 concurrent requests) | No crashes, reasonable response time |
| T9.5 | Dashboard after container crash | Tile shows stopped state, no JS errors |

---

## Execution Order

1. **T1** -- Lifecycle (foundation, must pass first)
2. **T2** -- Pause/Resume
3. **T3** -- Crash Recovery
4. **T8** -- Logging (verify logging works before deeper tests)
5. **T4** -- Profile Persistence
6. **T7** -- Resource Limits
7. **T9** -- Dashboard
8. **T5** -- Concurrent Access
9. **T6** -- Agent Identity (may require new features)

## Test Runner

Automated test script: `tests/test_integration.py`
Manual tests: marked with [MANUAL] in test ID

## Gaps Found (to build)

- [ ] No restart policy on containers (should be `unless-stopped` or healthcheck-based)
- [ ] No Docker HEALTHCHECK in image
- [ ] No agent identity tracking
- [ ] No per-agent quotas
- [ ] idle_pause_minutes not implemented (config exists but no watchdog)
- [ ] No container event logging (Docker events -> log file)
- [ ] No disk quota enforcement (config exists but not checked)
- [ ] No system resource pre-check before create

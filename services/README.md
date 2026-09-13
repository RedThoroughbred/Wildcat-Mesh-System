# Legacy units (v1)

These are the pre-v2 systemd units. **Superseded by `deploy/systemd/`** (hardened:
`Restart=on-failure`, `After=network-online.target`, `StartLimitIntervalSec=0`,
config validated in `ExecStartPre`, a `wildcat.target` to bounce the lot) and
installed by `deploy/install.sh`, which also disables these so two BBS processes
never fight over the node's single API socket. Kept for reference only.

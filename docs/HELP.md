# Help

## Show Help

```bash
csw --help
csw account --help
csw session --help
csw watcher --help
csw config --help
csw native --help
```

## Most Common Flows

### New account setup

```bash
csw account add PC
csw account login PC --email you@example.com
csw session discover PC
csw session list PC
csw session enable <session-id>
```

### Start monitoring

```bash
csw watcher run
```

or daemon mode:

```bash
csw watcher start
csw watcher status
```

### Live overview

```bash
csw dashboard
```

### Native desktop app + tray

```bash
csw native launch
```

### OS background service

```bash
csw native backend
csw native service-install
csw native status
```

### Troubleshooting

```bash
csw watcher doctor
csw watcher logs
csw watcher history --limit 20
```

For full reference, see [CLI_COMMANDS.md](CLI_COMMANDS.md).

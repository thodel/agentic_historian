# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## What Goes Here

Things like:

- Camera names and locations
- SSH hosts and aliases
- Preferred voices for TTS
- Speaker/room names
- Device nicknames
- Anything environment-specific

## Examples

```markdown
### Cameras

- living-room → Main area, 180° wide angle
- front-door → Entrance, motion-triggered

### SSH

- home-server → 192.168.1.100, user: admin

### TTS

- Preferred voice: "Nova" (warm, slightly British)
- Default speaker: Kitchen HomePod
```

## Why Separate?

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without losing your notes, and share skills without leaking your infrastructure.

---

Add whatever helps you do your job. This is your cheat sheet.

### Discord Mentions

When mentioning users in Discord, use the proper `<@USER_ID>` format for actual pings:

- **dh-bot** (me): `<@1519706253314756758>`
- **cl-bot**: `<@1474109314003108113>`
- **Tobias_H** (owner): `<@817396581317738546>`

Note: The text `@name` format does NOT trigger Discord notifications — only `<@USER_ID>` works.

### GPUStack / VLM OCR

- Endpoint: `https://gpustack.unibe.ch/v1`
- Model: `internvl3-8b-instruct`
- API key: stored in `.env.gpustack`
- Image size limit: ~65536 tokens input — images may need resizing before OCR
- Note: system messages with image content lists cause errors; put instructions in user message text instead
- Pipeline script: `ocr_pipeline.py` in workspace

## Related

- [Agent workspace](/concepts/agent-workspace)

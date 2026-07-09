# ── /mcp_propose command (P1-D2, #229) ──────────────────────────────────────

_mcp_probe_cache: dict = {}
"""In-flight probe results: url → {name, report, snippet, probe_errors,
author, channel_id, message_id, pr_url}"""


def _build_registry_snippet(name: str, url: str, report) -> str:
    """Build the MCPSource(...) snippet from a ProbeReport."""
    from utils.mcp_probe import registry_snippet
    return registry_snippet(name, url, report)


def _probe_mcp_sync(url: str):
    """Blocking wrapper: run mcp_probe.probe_sync inside a thread."""
    from utils.mcp_probe import probe_sync
    return probe_sync(url)


@bot.slash_command(
    name="mcp_propose",
    description="Probe a new MCP server and propose adding it to the registry via PR [#229]",
)
@require_role
async def mcp_propose_cmd(
    ctx,
    url: Option(str, "MCP server base URL (e.g. https://mcp.example.com)", required=True),
    name: Option(str, "Stable registry name (kebab-case, e.g. my-corpus)", required=True),
):
    """Probe ``url``, render the registry snippet, then offer a Confirm button that
    opens a PR against ``knowledge_hub/mcp_registry.py`` on the agentic_historian repo.
    """
    await ctx.defer()
    url = url.rstrip("/")

    # 1. Probe (blocking I/O — run in thread via _run_blocking)
    try:
        report = await _run_blocking(ctx, _probe_mcp_sync, url)
    except Exception as e:
        await ctx.followup.send(f"❌ Probe failed: {e}")
        return

    # 2. Generate registry snippet
    snippet = _build_registry_snippet(name, url, report)
    probe_errors = list(report.errors) if report.errors else []

    # 3. Store probe result (survives view timeout / restart)
    _mcp_probe_cache[url] = {
        "name": name,
        "report": report,
        "snippet": snippet,
        "probe_errors": probe_errors,
        "author": ctx.author.id,
        "channel_id": ctx.channel_id,
        "message_id": None,
        "pr_url": None,
    }

    # 4. Render report + Confirm button
    L = [
        f"**🔍 MCP-Probe: `{url}`**",
        "",
        "```python",
        snippet,
        "```",
    ]
    if probe_errors:
        L.append("*Warnungen:* " + " | ".join(probe_errors))
    L.extend([
        "",
        "Review the snippet. If correct, click **✅ Confirm** to open a PR. "
        "The PR adds the entry to ``knowledge_hub/mcp_registry.py``.",
        "**⚠️ After merging:** run ``python -m knowledge_hub.mcp_registry`` to validate.",
    ])
    content = "\n".join(L)

    btn = Button(style=ButtonStyle.success, label="✅ Confirm — Open PR",
                 custom_id=f"ah:mcp_propose_confirm:{url}")
    view = View(timeout=None)
    view.add_item(btn)

    msg = await ctx.followup.send(content, view=view)

    if msg is not None:
        _mcp_probe_cache[url]["message_id"] = msg.id
        try:
            from persistent_views import store_message_id
            store_message_id(
                {"_doc_id": f"mcp_propose:{name}", "pipeline": {}},
                "confirm",
                msg.id,
            )
        except Exception as e:
            logger.warning(f"[mcp_propose] could not persist view: {e}")


# ── Persistent Confirm button for /mcp_propose ───────────────────────────────

class _ConfirmMCPView(View):
    """Persistent view re-registered on startup so button clicks survive restarts."""

    def __init__(self, url: str):
        super().__init__(timeout=None)
        self.url = url
        entry = _mcp_probe_cache.get(url, {})
        self.snippet = entry.get("snippet", "")
        self.probe_errors = entry.get("probe_errors", [])
        self.name = entry.get("name", url)

    @discord.ui.button(label="✅ Confirm — Open PR", style=ButtonStyle.success,
                       custom_id="ah:mcp_propose_confirm")
    async def confirm(self, button: Button, interaction: discord.Interaction):
        """Open the PR after the user clicks Confirm."""
        await interaction.response.defer(ephemeral=True)

        # Role check
        allowed_role_id = getattr(config, "REQUIRED_DISCORD_ROLE_ID", None)
        if allowed_role_id:
            author_role_ids = {role.id for role in interaction.user.roles}
            if allowed_role_id not in author_role_ids:
                await interaction.followup.send(
                    "⛔ Du hast nicht die erforderliche Rolle für diesen Befehl.",
                    ephemeral=True,
                )
                return

        entry = _mcp_probe_cache.get(self.url)
        if entry is None:
            await interaction.followup.send(
                "❌ Cache miss — please re-run /mcp_propose.", ephemeral=True,
            )
            return

        snippet = self.snippet
        probe_errors = self.probe_errors
        name = self.name

        # Build PR body
        transport = entry["report"].transport or "?"
        contract = entry["report"].contract or "?"
        contract_tool = entry["report"].contract_tool or "?"
        server_info = entry["report"].server_info
        server_info_str = (
            f"`{server_info.get('name', '')}` (`{server_info.get('version', '')}`)"
            if server_info else "unknown"
        )
        body_lines = [
            f"## MCP Source Proposal: `{name}`",
            "",
            f"**URL:** {self.url}",
            f"**Transport:** {transport}",
            f"**Server:** {server_info_str}",
            f"**Contract:** {contract} (tool: `{contract_tool}`)",
            f"**Probe errors:** {' | '.join(probe_errors) if probe_errors else 'none'}",
            "",
            "## Registry Entry",
            "",
            "```python",
            snippet,
            "```",
            "",
            "## Validation",
            "",
            "After merging, run: ``python -m knowledge_hub.mcp_registry``",
        ]
        pr_body = "\n".join(body_lines)
        title = f"feat(mcp): add `{name}` MCP source (#229)"
        branch = f"mcp-propose-{int(time.time())}"
        file_path = "knowledge_hub/mcp_registry.py"

        # Read current registry from main branch
        s = _gh_session()
        try:
            r = s.get(
                f"https://api.github.com/repos/{config.GITHUB_REPO}/contents/{file_path}",
                params={"ref": "main"},
                timeout=30,
            )
            if r.status_code != 200:
                await interaction.followup.send(
                    f"❌ Could not read current registry: {r.status_code}", ephemeral=True,
                )
                return
            current_content = base64.b64decode(r.json()["content"]).decode("utf-8")
        except Exception as e:
            await interaction.followup.send(f"❌ GitHub API error: {e}", ephemeral=True)
            return

        new_content = current_content.rstrip() + "\n\n" + snippet + "\n"

        from utils.publish_github import commit_files_to_new_branch, create_pr

        files = {file_path: new_content.encode("utf-8")}
        commit_url, _ = commit_files_to_new_branch(
            files=files,
            message=f"feat(mcp): add `{name}` source via /mcp_propose",
            branch_name=branch,
            base_branch="main",
            repo=config.GITHUB_REPO,
        )
        if not commit_url:
            await interaction.followup.send(
                "❌ Failed to commit to new branch. Check logs.", ephemeral=True,
            )
            return

        pr = create_pr(
            title=title,
            body=pr_body,
            head_branch=branch,
            base="main",
            repo=config.GITHUB_REPO,
        )
        if pr is None:
            await interaction.followup.send(
                "⚠️ Branch committed but PR creation failed — a PR may already be open. "
                f"Branch: `{branch}`. Please open the PR manually.", ephemeral=True,
            )
            return

        entry["pr_url"] = pr["html_url"]

        await interaction.followup.send(
            f"✅ PR opened: {pr['html_url']}\n"
            f"Once reviewed and merged, run ``python -m knowledge_hub.mcp_registry`` to validate.",
            ephemeral=False,
        )


def _gh_session():
    """Return a requests.Session with GitHub API headers."""
    import requests as _req
    s = _req.Session()
    s.headers.update({
        "Authorization": f"Bearer {config.GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    return s


def _register_mcp_propose_views() -> None:
    """Re-register Confirm views for any in-flight /mcp_propose sessions.

    Called from ``on_ready`` so button clicks survive bot restarts.
    """
    count = 0
    for url, entry in list(_mcp_probe_cache.items()):
        if entry.get("pr_url"):
            continue  # Already resolved
        msg_id = entry.get("message_id")
        view = _ConfirmMCPView(url)
        bot.add_view(view, message_id=msg_id)
        count += 1
    if count:
        logger.info(f"[mcp_propose] re-registered {count} persistent view(s)")
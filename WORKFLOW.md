# WORKFLOW.md — Git Branching, Merging, and Dual-Mirror Sync

> **Related docs:** [`README.md`](./README.md) (project overview) · [`SETUP.md`](./SETUP.md) (clone + local dev setup)

**Audience:** anyone (human or AI agent) committing to this repo. Reading this once prevents the dual-merge SHA divergence that bit us 2026-05-02.

---

## The two-mirror setup

This repo lives on TWO remotes:

- **GitLab** (`origin`) — `https://labs.gauntletai.com/coryvandenberg/agentforge.git` — **primary review surface; what GauntletAI graders see**
- **GitHub** (`github`) — `https://github.com/TradeUpCards/agentforge.git` — public mirror (insurance + portfolio)

`origin` is configured with **two push URLs** so a single `git push origin <branch>` updates both remotes at the same SHA. Verify:

```bash
git remote -v
# origin   <gitlab-url>  (fetch)
# origin   <gitlab-url>  (push)
# origin   <github-url>  (push)   ← second push URL
# github   <github-url>  (fetch / push)   ← standalone mirror remote, fallback
```

If you don't see two `(push)` lines for `origin`, run:

```bash
git remote set-url --push origin <gitlab-url>
git remote set-url --add --push origin <github-url>
```

---

## Daily workflow — feature branches

Standard:

```bash
git checkout master
git pull origin master              # fetches from gitlab fetch URL
git checkout -b agentforge/<slug>   # branch name pattern: agentforge/<short-description>
# ... do work ...
git add ...
git commit -m "<conventional commit message>"
git push -o merge_request.create \
         -o merge_request.target=master \
         -o "merge_request.title=<title>" \
         origin agentforge/<slug>
```

**Why the `-o merge_request.create` flags:** GitLab supports push options that auto-create the MR as part of the push. Without this, you get a hint URL in the push output and have to click through to create. With it, the MR exists by the time the push command returns.

GitHub has no equivalent push option. If you also want a GitHub PR (see "When to dual-PR" below), create it after the push:

```bash
gh pr create --repo TradeUpCards/agentforge --base master --head agentforge/<slug> \
  --title "<title>" --body "<body>"
```

---

## **The critical rule — DO NOT dual-merge via two UIs**

If you open both a GitLab MR AND a GitHub PR for the same branch and merge BOTH via the platform UIs, **you create two different merge commits with different SHAs**. Same content, different commit graph. Permanent divergence between the two mirrors.

This bit us on 2026-05-02 with the `agentforge/ci-cd-workflows` branch — local master ended up "ahead 1, behind 4" of GitHub master, with both differing from GitLab master.

### The right pattern: merge ONE place, mirror via CLI

For most changes (especially pure docs, any change without significant CI gating value), the workflow is:

1. **Open GitLab MR only** (auto-created by the push command above)
2. **Merge GitLab MR via the GitLab UI** — GitLab master gets merge commit `MA`
3. **From your local clone, mirror to GitHub:**
   ```bash
   git checkout master
   git fetch origin                        # local sees origin/master at MA
   git merge --ff-only origin/master       # local master moves to MA
   git push github master                  # GitHub master moves to MA
   ```
4. **Delete the orphan GitHub branch:**
   ```bash
   git push github --delete agentforge/<slug>
   ```

After step 4, both remote masters are at the **same commit object** (MA). Zero divergence.

Mechanic: git commits are content-addressed (the SHA is a hash of the commit + parents + content). Pushing commit MA from local to GitHub stores the same object — not a copy, not a re-parented version.

### When dual-PR is acceptable

The dual-PR pattern is OK when **GitHub Actions CI provides real value** that you want gated on the merge — e.g., a code change that should run the eval workflow before landing. In that case:

1. Open BOTH the GitLab MR (via push option) and the GitHub PR (via `gh pr create`)
2. Wait for GitHub's CI to go green
3. Merge BOTH UIs
4. Accept the SHA divergence — it's permanent but cosmetic, content is identical

**Don't do this for pure docs / config / no-CI-value changes.** The divergence overhead isn't worth it.

---

## Branch hygiene

After a branch is merged:

```bash
# Delete locally
git branch -D agentforge/<slug>

# If pushed to github standalone (orphan after mirror sync), delete remotely
git push github --delete agentforge/<slug>
```

GitLab usually auto-deletes the source branch on merge if "Delete source branch" was checked in the MR. GitHub PR merge has the same option. Either way, prune locally with:

```bash
git fetch --all --prune
```

---

## Worktrees for parallel work (optional)

If multiple agents are working on the same repo simultaneously, use git worktrees to avoid stepping on each other's working directory:

```bash
# Create a second physical checkout on a new branch
git worktree add ../AgentForge-<slug> -b agentforge/<slug>

# Work in the second checkout, leaving the main checkout untouched for the other agent
git -C ../AgentForge-<slug> add ...
git -C ../AgentForge-<slug> commit -m "..."
git -C ../AgentForge-<slug> push ...

# Tear down when done
git worktree remove ../AgentForge-<slug>
git branch -D agentforge/<slug>   # local cleanup if needed
```

Both worktrees share the same `.git/` directory (refs, objects, hooks) but each has its own HEAD pointer + working files. Pre-commit hook runs in both. Branches created in one worktree are visible from the other (they're just refs).

**Constraint:** the same branch can only be checked out in one worktree at a time — feature branches per worktree, not master in multiple.

---

## Pre-commit hook

`scripts/git-hooks/pre-commit` runs verifier unit tests + smoke-tier eval cases on every commit (~4-5 seconds, fixture mode, no LLM cost). Install once per clone:

```bash
git config core.hooksPath scripts/git-hooks
```

Bypass in emergencies with `git commit --no-verify` — but the GitHub Actions workflow `agent-eval.yml` re-runs the same tests as defense-in-depth, so a bypass that introduces a failure surfaces on the PR.

---

## TL;DR for AI agents reading this

If you're a Claude / GPT / etc. session about to commit and push:

1. **Use `git push -o merge_request.create -o merge_request.target=master -o "merge_request.title=..." origin <branch>`** — auto-creates the GitLab MR
2. **Don't open a GitHub PR** unless GitHub CI gating actually adds value (rare)
3. **After the user merges the GitLab MR**, mirror to GitHub via `git push github master` (after `git fetch origin && git merge --ff-only origin/master`)
4. **Don't merge the same branch via both UIs** — that's what caused the SHA divergence on 2026-05-02
5. **Use a worktree** if another agent is in flight on the same repo (don't share working directories)

---
name: payer-in-the-loop
description: Draft an edition of the LinkedIn newsletter "Payer in the Loop" — a ~1,500-word, statistics-dense weekly teardown of a startup idea that opens with narrative, argues from data, ends with a verdict, and closes with a dated reference list. Use this skill whenever the user asks for a newsletter, a LinkedIn newsletter edition, a weekly edition, an idea teardown, a companion feed post, or says things like "write this up for the newsletter", "turn this research into a newsletter", "this week's edition", or names a topic and asks to publish it. Also use it when they mention "Payer in the Loop" by name, ask for an edition title, or ask to adapt existing startup research into something postable. Trigger even when the request is as short as "newsletter on GPU cloud" — do not wait for the user to describe the format.
---

# Payer in the Loop — LinkedIn newsletter

Produces one weekly edition of the user's LinkedIn newsletter, plus the companion feed post that drives traffic to it.

**Newsletter name:** Payer in the Loop
**Standing description:** *Weekly teardowns of startup ideas — market data, unit economics, and the one question: who pays, and from when?*

The newsletter's reason to exist is that it prints numbers other people won't and reaches a verdict other people won't. Two failure modes destroy it: fabricated statistics, and hedged conclusions. Most of what follows exists to prevent those two things.

---

## Step 1 — Find the research before writing a word

Never generate a statistic. Every number must trace to research already done. The user has months of startup research in this project; an edition is a compression of that work, not a new opinion.

Before drafting:

1. `view /mnt/project/` and read every doc plausibly related to the topic. These are the primary source.
2. `conversation_search` using content words from the topic (e.g. `IndiaMART directory monetization`, `GPU cloud breakeven`, `corporate furnished rentals GST`) — not meta-words like "research" or "we discussed".
3. `recent_chats` if the user anchors the topic in time ("the thing from last week").
4. If the user attached or named a document, read that first and treat it as authoritative.
5. Web-search only to date-check or fill a specific gap the research left open — and flag anything that has moved since the original session.
6. Resolve the edition number now — see Step 7. The same `ls` and `conversation_search` that surface prior editions for voice calibration also give you the number, so do it in one pass rather than at delivery.

**Prior editions on the same topic.** If Step 1 turns up an edition already covering this ground, say so before drafting and name what the new one adds. Two editions arguing the same thesis from the same figures is worse than one — the second should either be a dated follow-on built on events the first predates, or it should not ship. A follow-on defers to the earlier edition rather than re-running its arguments.

**Gate:** an edition of this length needs 15–25 sourced figures. If the research yields fewer than about ten, stop and say so. Offer to pick a different topic or run the underlying research first.

**Traceability rule:** if a number cannot be pointed back to a project doc, a past research session, or a source found in this session, cut it. Do not estimate from general knowledge and do not round up from memory. Where a figure genuinely is the user's own modelling, label it — "my estimate", "directional".

---

## Step 2 — Fix the verdict before drafting

Decide the conclusion first, then write toward it. Exactly one of:

- **No-go** — the model is structurally broken; name the constraint that kills it
- **Conditional go** — viable only if a named condition holds; state the condition and the number attached to it
- **Go** — rare; reserve it, and still name the kill risk

The verdict appears at the *end* of the edition, but it governs everything before it. Each section lays track toward it. If the verdict is unclear after reading the research, the edition isn't ready — say so rather than writing a survey.

---

## Step 3 — Write the edition

**Length: 1,400–1,600 words.** Long enough to argue properly, short enough to finish on a phone.

Structure, in this order:

**1. Title** — ≤100 characters. Lead with the number, the collision, or the killed assumption.

**2. Opening — 150–250 words, no heading.** This is the narrative block and the only place prose runs free. Set the situation: what is happening in the market, why the opportunity looks obvious, and the first number that complicates it. Narrative here means *flowing analytical prose*, not a personal anecdote — the user prefers statistics to stories, so the scene is set with facts, not with "last week I met a founder". End the opening on the turn: the reason to be suspicious.

**3. Four headed sections, 250–350 words each.** Adapt the headings to the topic; the underlying job of each is stable:

- *What's actually real* — the demand, sized and dated. Also name what has **no** published figure; that absence is often the finding.
- *Why the obvious version fails* — the structural blocker, or the graveyard of companies that already tried it.
- *The economics, and who pays* — unit economics, take rate, cost to serve, and the named paying counterparty with the month revenue starts.
- *What would have to be true* — the conditions, and the risks with an owner attached to each.

Prose paragraphs carry the argument. Use bullets only where a list genuinely is a list — a run of comparable figures, a set of conditions. Roughly one bulleted run per section at most.

**4. The verdict — 150–220 words, final heading.** Bold one-liner: No-go / Conditional go / Go. Then the conditions, numbered where there is more than one, each with a figure attached. Then the single risk most likely to kill it.

**5. What I'd validate next — 60–100 words.** Two or three specific, cheap, time-boxed actions with numeric gates.

**6. References** — see Step 4.

Title formulas that work:
- The number that kills it: `2.5%: the ceiling that ends the directory business`
- The collision: `₹255 breakeven against ₹65 subsidised`
- The killed assumption: `1.5 billion people is not a data moat`
- The absence: `Nobody owns the 1–6 month furnished flat. There's a reason.`
- The outcome gap: `$110M raised, $11M sold: why nobody owns India's monthly furnished flat`

**The title carries the turn or the number — never the opportunity alone.** An opportunity headline attracts the audience this newsletter is differentiating itself from, and it sets up a bait-and-switch when the reader arrives at a conditional or a no-go. `A billion-dollar gap that nobody has filled` is the counter-example: it promises a market, where `a gap this size is usually not an oversight — it is a structural refusal` promises analysis. Include at least one topic keyword so the title is findable in search and legible to a scrolling reader.

Do not promote a phrase from the body into the title without rewriting the body line. The echo makes the original land flat 200 words later.

Read `references/voice.md` before drafting, and `references/example-edition.md` for a full worked edition to write against.

---

## Step 4 — The reference section

Every edition ends with a dated source list. This is the credibility mechanism — it is what separates the newsletter from opinion posts, and it is what makes the numbers reusable later.

Format: a flat list, one line per source, grouped only if there are more than about ten.

```
**References**

- Zinnov–Nasscom GCC Landscape 2026 — GCC count, headcount, revenue (FY26)
- Entrackr, FY24 filings — NoBroker revenue
- Forbes India — NestAway distress sale (2023)
```

Rules:
- Name the source, then what it supports, then the date of the data.
- No bare URLs in the body. If a link belongs anywhere it is here, and LinkedIn renders it inline.
- If a figure came from the user's own modelling, list it as such: `Author's model — landed capex, 8× H100 (Jul 2026)`.
- If a source could not be verified, either cut the claim or mark it: `unverified — flagged in text`.

---

## Step 5 — LinkedIn formatting constraints

The LinkedIn article editor is not markdown. Get this wrong and the edition renders as garbage.

- **No tables.** The research docs are table-heavy; convert every table to prose or one-line bullets.
- Subheads only (rendered as H2) — no deeper nesting.
- Paragraphs of 3–4 sentences. At 1,500 words, wall-of-text is the main way to lose a mobile reader.
- Bold reserved for the verdict line and a handful of pivotal figures.
- No emoji in the title or opening; at most one in the body, and only if it's doing work.

Numbers, formatted consistently:
- Date every figure: `(FY26)`, `(Jul 2026)`, `(Q1 FY27)`. A statistics newsletter that doesn't date its statistics ages into a liability.
- Rupees as `₹`. Convert at **1 USD = ₹95** whenever the figure matters to an overseas reader — the audience deliberately includes US and Middle East buyers.
- **One currency per comparison.** Never write `$110M raised, ₹90 Cr sold` — mixing units inside a single collision forces the reader to do arithmetic before the point lands. Pick a unit and convert the other.
  - Default to **USD** when the comparison is raise-versus-outcome, when either figure is natively dollar-denominated, or when the line is a title. `$110M raised, $11M sold` reads as a clean 10x anywhere in the world.
  - Default to **₹** when both figures are Indian operating numbers — revenue, rent, ARPU, price per GPU-hour — where rupees are the native unit and converting obscures rather than clarifies.
  - Converting a multi-year fundraise at today's rate is defensible in a reference line, not in a headline. If a converted historical figure appears, mark it directionally.
- Percentages and multiples exact as sourced; don't smooth them.
- **Keep haircut denominators straight.** Raised-versus-sold and peak-valuation-versus-sold are different claims with different multiples (NestAway: 10x on the raise, ~20x on the $220M peak). State which one you mean; never let a title pull the body into conflating them.

---

## Step 6 — Companion feed post

Every edition ships with a separate feed post that links to it. LinkedIn does not distribute newsletters on its own, so this post is the entire distribution mechanism.

**Length: 400–800 characters.** Short, and deliberately incomplete.

The job is not to summarise the edition — it is to open a question the edition answers. A feed post that delivers the argument and the verdict has given the reader no reason to click; they have already got the value, and the newsletter gets a like instead of a subscriber. Withhold on purpose.

Shape:

1. **Line one: the hardest collision in the edition, in under 140 characters.** That is all that shows above the fold on mobile. A number against a number is best — `$110M raised. $11M sold.`
2. **Two or three short lines establishing the contradiction.** The demand is real *and* the attempts died. The market is large *and* nobody owns it. Two or three figures maximum — the edition has thirty, and spending them here is what kills the click.
3. **One line naming what is withheld, specifically.** The verdict and its conditions, the constraint that killed the model, the identity of the payer. Name the shape of the answer, never the answer.
4. **One line pointing to the edition.**
5. Hashtags: three at most, precise, or none.

**Suspense, not clickbait.** The withheld thing must be real, specific and delivered in full by the edition. `The verdict, and the four conditions attached to it, are in this week's edition` is right. `You won't believe what I found` is wrong, and so is any rhetorical question stack. If the edition would disappoint someone who clicked on the promise, rewrite the promise.

Never state the verdict in the feed post. That is the single rule that separates this from a summary.

**Example — the shape working:**

```
NestAway raised over $110M to fix Indian rental housing. It sold for $11M.

The gap it was chasing is still open. India's GCCs add ~300,000 jobs a
year, and every one of those seats is someone who needs a furnished flat
for one to six months. Airbnb is built for nights. NoBroker is built for
11-month leases. Nobody sells the middle.

Zeus, Landing and OYO Life all raised nine figures against that same gap.
All four died the same death — and it is the same reason a corporate
travel desk cannot book an Airbnb in India today.

The verdict, and the four conditions it depends on, are in this week's
Payer in the Loop.
```

Note what that does not contain: the verdict, the GST mechanism, the unit economics, or the name of the payer. Three figures, one contradiction, one specific withheld answer.

---

## Step 7 — Deliver

Every edition is numbered. The newsletter is weekly, so the number *is* the week: the edition shipped in week 3 is `w03`, the next one is `w04`. Filenames carry it, zero-padded to two digits:

- `pitl-w<NN>-<slug>.md` — the edition
- `pitl-w<NN>-<slug>-feedpost.md` — the feed post

So week 4 on a GPU-cloud teardown ships as `pitl-w04-gpu-cloud.md` and `pitl-w04-gpu-cloud-feedpost.md`.

**Resolve the number in Step 1, not here.** Writing to the wrong number is cheap to prevent and annoying to fix after the files exist. Work down this list and stop at the first answer:

1. The user stated it ("this is week 4", "next edition"). Their number always wins.
2. `ls /mnt/user-data/outputs/` and take the highest `pitl-w<NN>-` present, then add one.
3. `conversation_search` for `Payer in the Loop edition` and count distinct editions delivered.
4. Ask. One question, before drafting.

Two rules that follow from this:

- **Never ship an unnumbered file.** There is no `pitl-<slug>.md` fallback. If the number genuinely cannot be resolved, ask rather than guess — an unnumbered edition breaks the sort order for every edition after it.
- **Never renumber or rename a past edition** without the user asking. Older files may predate this convention; leave them.

Then `present_files` and report five lines only: edition number, word count, count of sourced figures, number of references, the verdict. No summary — the user is about to read it.

---

## Guardrails

**Aim the knife at models, not at people.** Published financials, traffic declines and shutdowns are fair game. Character judgments about named founders are not. The user is building a venture and a reputation with overseas buyers in the same feed; an edition that reads as a hit piece costs more than it earns.

**Don't leak the user's own live plans.** Anything in active build gets written about only when the user asks explicitly, and then only at the level of detail they set.

**Honest uncertainty is on-brand; hedging is not.** "No published figure exists for annual corporate relocations in India — that gap is why the wedge is unowned" is right. "This could be interesting, but there are challenges" is wrong.

**Statistics over stories, always.** If a sentence could be replaced by a number, replace it. The narrative opening is a structural setup, not an anecdote.

---

## Files

- `references/voice.md` — voice rules and banned constructions. Read before drafting.
- `references/example-edition.md` — a full worked ~1,500-word edition. Read before drafting.
- `assets/edition-template.md` — the skeleton to fill in.

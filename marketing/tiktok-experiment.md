# TikTok organic experiment

## Objective

Find one repeatable organic format that produces qualified Mac App Store intent
at zero ad spend. The operating threshold is about one net sale per week; views
are useful only when they improve retained viewing, profile visits, native
outbound clicks, downloads, or proceeds.

## Creative rules

- Use 11–15 seconds, vertical 1080x1920, with product proof in the first frame.
- Film the real macOS menu, Settings, wallpaper change, or a real desk/display.
- Use 3–5 cuts. Keep the hook readable in the first three seconds.
- Add voiceover or licensed Commercial Music Library sound when scheduling.
- Use the **Your brand** disclosure on every post.
- Default CTA: **Search EaselWall on the Mac App Store**.
- Use `https://easelwall.com/tiktok/` only after a clickable profile website
  field is visibly available.
- Rotate one of the three 3-tag sets from `videos.json`; do not add `#fyp`.
- Schedule through TikTok Studio or Web Business Suite. Do not automate follows,
  comments, messages, or engagement.

## Twelve-post matched-pair matrix

Each value proposition gets a direct demo and a founder/desk execution, with
two hooks. Keep the product proof and CTA constant within each pair.

| Posts | Value proposition | Direct demo | Founder or desk execution | Hook A | Hook B |
|---|---|---|---|---|---|
| 1–4 | Proper composition | Screen recording: cropped wallpaper, then EaselWall mat | Vertical-monitor desk reveal | Mac wallpapers keep cropping paintings like this. | How I make my Mac feel like a museum in 10 seconds. |
| 5–8 | Every display | Displays pane, then three distinct desktops | Three-monitor desk reveal | Three monitors should not share one wallpaper. | Portrait monitor owners: I fixed the wallpaper problem. |
| 9–12 | Honest one-time utility | Click Next Painting, render, desktop reveal | Founder explains the $99 annual fee target | $2.99. No subscription, account, ads, or tracking. | I paid Apple $99 to sell this $2.99 Mac app. My goal: 55 sales a year so it pays for itself. |

## Four-week matched schedule

Record these identifiers unchanged in `experiment-template.csv`. This schedule
covers every value proposition × execution style × hook combination exactly
once, while mixing the cells across weeks so a single week is not confused with
one treatment.

| Post | Week | concept_id | value_proposition | execution_style | hook_variant | Proof sequence |
| ---: | ---: | --- | --- | --- | --- | --- |
| 1 | 1 | `composition_direct_a` | `composition` | `direct_demo` | `A` | Cropped wallpaper → Museum Mat → desktop reveal |
| 2 | 1 | `display_founder_a` | `every_display` | `founder_desk` | `A` | Founder at three-monitor desk → three distinct paintings |
| 3 | 1 | `utility_direct_a` | `honest_utility` | `direct_demo` | `A` | $2.99 listing → Next Painting → changed desktop |
| 4 | 2 | `composition_founder_a` | `composition` | `founder_desk` | `A` | Vertical-monitor crop → founder enables mat → reveal |
| 5 | 2 | `display_direct_a` | `every_display` | `direct_demo` | `A` | Displays pane → unique-per-display toggle → desktops |
| 6 | 2 | `utility_founder_a` | `honest_utility` | `founder_desk` | `A` | Founder states one-time price → real menu proof |
| 7 | 3 | `composition_direct_b` | `composition` | `direct_demo` | `B` | Edge-to-edge crop → mat spacing choices → reveal |
| 8 | 3 | `display_founder_b` | `every_display` | `founder_desk` | `B` | Portrait monitor first → pan to landscape monitors |
| 9 | 3 | `utility_direct_b` | `honest_utility` | `direct_demo` | `B` | No account/ads/subscription text → real wallpaper change |
| 10 | 4 | `composition_founder_b` | `composition` | `founder_desk` | `B` | Founder frames the crop problem → composed desk reveal |
| 11 | 4 | `display_direct_b` | `every_display` | `direct_demo` | `B` | Orientation-aware settings → portrait and landscape proof |
| 12 | 4 | `utility_founder_b` | `honest_utility` | `founder_desk` | `B` | Founder explains the 55-sales goal → $2.99 listing proof |

Freeze the App Store listing, $2.99 price, CTA, profile destination, and hashtag
rotation policy for all 12 posts. If an urgent correction changes one of those
controls, mark the break in the ledger and analyze the periods separately.

## Measurement

Copy `experiment-template.csv` to a private file under
`marketing/experiments/`. Add observations at 2, 24, and 72 hours. Do not edit
old observations; append a new row so decisions remain auditable.

Primary early signals:

1. Average watch seconds and completion rate.
2. Profile visits per 1,000 views.
3. Native outbound clicks per 1,000 profile visits, once a profile link exists.
4. App Store first-time downloads and purchases after Apple exposes enough data.

Advance a format only when it improves a downstream rate, not because it has
the highest raw views. Change one major variable at a time after the first
12-post matrix.

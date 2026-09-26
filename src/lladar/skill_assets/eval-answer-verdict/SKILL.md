---
name: eval-answer-verdict
description: Judge each LLaDAR response for answer correctness.
---

# Answer verdict

First submit a plan with a boolean `correct` dimension. For each assigned trial,
compare only its expected answer and actual response, then submit one grounded
judgment. Do not calculate aggregate statistics.

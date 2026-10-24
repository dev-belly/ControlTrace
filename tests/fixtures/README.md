# Legacy replay fixture

`controltrace-0.2.8-v3.zip` was exported with the unmodified 0.2.8 implementation
at commit `58b03c1aa0d23778753fd1917fb121a8c98fe77c` before changing CSV policy.
SHA-256: `43e1ab5af2bf6992ce49186ae55bd4d6ae409ccd5c570299f2c1e1901b2bf082`.

It contains the default synthetic source snapshot and one programmatically
created test review, not a real person's audit decision. The review note contains
an ASCII formula prefix, embedded CR/LF and fullwidth text. Its CSV uses the
historical apostrophe policy. The regression must replay these retained bytes
under the format-3 policy, while new format-4 exports use visible `text: ` prefixes.

`controltrace-0.2.9-v4-timeline.zip` was exported by the unmodified 0.2.9 code at
`20c0bcfe33e23a95a3c0f95975d86474800fcd26` before changing timeline ordering.
SHA-256: `85401d33aee4c20b81469d58b505d07658a1f3c8c0b57c27a184b6906ea069c9`.
Three synthetic CH002/D002 timestamps use mixed UTC offsets, and one test review
is programmatically recorded. Original lexical order places deployment before
approval. Format-4 replay must preserve its historical bytes and review binding,
with an explicit legacy-order notice. A current UTC-order replay must require
a new review for the corrected finding representation.

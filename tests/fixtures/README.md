# Legacy replay fixture

`controltrace-0.2.8-v3.zip` was exported with the unmodified 0.2.8 implementation
at commit `58b03c1aa0d23778753fd1917fb121a8c98fe77c` before changing CSV policy.
SHA-256: `43e1ab5af2bf6992ce49186ae55bd4d6ae409ccd5c570299f2c1e1901b2bf082`.

It contains the default synthetic source snapshot and one programmatically
created test review, not a real person's audit decision. The review note contains
an ASCII formula prefix, embedded CR/LF and fullwidth text. Its CSV uses the
historical apostrophe policy. The regression must replay these retained bytes
under the format-3 policy, while new format-4 exports use visible `text: ` prefixes.

# Findings service fixture

`ami_findings.json` is the **assumed** response of the internal findings service ③ for one AMI,
documented in `docs/specs/reports.md` (D-37). It is not an AWS API and there is no official sample
to copy yet; the shape comes from the agreed spec, and the recorded response must be replaced with
a real one before go-live (DESIGN §15). When that happens, keep the same file name so the tests and
`devtools/fake_aws` can keep reading it.

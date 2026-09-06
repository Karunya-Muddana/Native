# Terms and disclaimer

Native is software you download and run yourself. There is no hosted service and
no account, so this is not a subscriber agreement — it is the set of conditions
attached to using the software, alongside [LICENSE](LICENSE),
[PRIVACY](PRIVACY.md) and [TRADEMARKS](TRADEMARKS.md).

Using or distributing it means accepting what follows.

## No warranty

Apache 2.0 §7 and §8 already say this, and it is repeated here because it
matters more than usual for this kind of software:

**The software is provided "as is", without warranty of any kind.** No guarantee
that it works, that it is fit for any purpose, that it is available, or that it
is correct. You bear the entire risk of using it.

## Its output is not advice, and it is not verified

This is the part to read twice.

Native is built around large language models. **Models produce confident,
fluent, incorrect output.** They misread numbers, they invent citations, and
they will state a wrong conclusion in exactly the tone of a right one. The run
trace and the file read-back exist to make that visible — not to prevent it.

Nothing Native produces is professional advice. In particular it is **not**:

- engineering, inspection, structural, or safety advice
- legal, financial, medical, or regulatory advice

The project began as an industrial inspection assistant, so this needs saying
plainly: **do not make a safety, compliance, or engineering decision on its
output.** A qualified person must independently verify anything that matters
before it is acted on. If a wrong answer could hurt somebody or breach a
regulation, Native is a drafting aid and nothing more.

## Your responsibilities

- **What you feed it.** You must have the right to process the documents and
  data you give it, and to send them to whichever provider is configured. See
  [PRIVACY](PRIVACY.md) for what gets sent where.
- **Provider terms.** Groq, Google, OpenRouter, NVIDIA and any other configured
  provider each have their own terms and acceptable-use policies. You are the
  account holder; you are bound by them, and responsible for the usage and any
  charges on your keys.
- **Code execution.** Native runs model-written code on your machine. The Docker
  sandbox disables networking and caps memory — it is a serious mitigation, not
  a guarantee, and it depends on your Docker configuration being sound. Do not
  run it on a machine where a container escape would be catastrophic.
- **Tools that reach out.** The agent can fetch web pages, write files into the
  workspace, open applications on your desktop, and delete workspace contents.
  Those are the capabilities you installed. Review what it plans to do.
- **Your keys and your data.** Keys sit in `.env` in plain text. Back up
  anything you cannot lose; `clear_workspace` is irreversible and there is no
  trash.

## Limitation of liability

To the maximum extent permitted by law, the authors and contributors are not
liable for any damages arising from use of the software — including lost data,
lost profits, business interruption, or decisions taken on its output — whether
or not they were advised such damages were possible.

## Acceptable use

Do not use Native to break the law, to infringe others' rights, to generate
material that is illegal or that impersonates a real person or organisation, or
in a way that violates the terms of any model provider you have configured.

## Brand

The code is Apache 2.0. The name and mark are not — see
[TRADEMARKS](TRADEMARKS.md). Fork freely; rename when you do.

## Changes

Versioned with the code. `git log TERMS.md` is the change history. Continuing to
use a newer version means accepting the terms shipped with it.

---

*Last updated: 2026-09-06. Not legal advice, and not drafted by a lawyer. It is
a clear, honest statement of the position for a self-hosted open-source project.
If you deploy Native for other people or inside a company, get these reviewed.*

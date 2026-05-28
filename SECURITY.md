# Security Policy

## Reporting a vulnerability

If you've found a security issue in this repository, **please don't open a
public GitHub Issue**. Report it privately so we can investigate and ship a
fix before details are public.

The preferred channel is GitHub's private security advisory flow:

1. Go to the [Security tab](../../security) of this repository.
2. Click **Report a vulnerability**.
3. Fill in the form with reproduction steps and impact.

We will acknowledge receipt within a few business days and keep you updated
as we work on a fix. Once a fix is shipped we'll credit you in the release
notes unless you'd rather stay anonymous.

## Scope

This is a collection of *example projects*. The code is intended as a
starting point you fork and adapt, not as production infrastructure. The
classes of issue we care most about:

- Credentials or secrets accidentally committed to the repository.
- Code patterns the examples teach that would be unsafe in a real
  production fork (e.g. unsafe defaults, missing auth checks, command
  injection in setup scripts).
- Vulnerabilities in our own setup scripts or Dockerfiles.

Vulnerabilities in third-party dependencies should be reported upstream;
we'll pick up the fix when it's released and bump the lockfile.

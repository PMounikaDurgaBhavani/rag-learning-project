---
article_id: HC-002
product_area: Authentication
last_updated: 2026-08-05
---

# Account Locked After Failed Login Attempts

## Overview

CloudDesk temporarily locks an account when multiple unsuccessful login attempts are detected.

This security feature helps protect accounts from repeated unauthorized login attempts.

## Why an Account Becomes Locked

An account may become temporarily locked after several incorrect password attempts.

Common reasons include:

- Entering an incorrect password multiple times.
- Using an old password after changing the password.
- Using saved credentials from a password manager.
- Attempting to log in from an application that has stored an outdated password.

## What Happens When an Account Is Locked

When an account is temporarily locked, CloudDesk prevents additional login attempts for a short period.

The user should wait for the temporary lock period to expire before trying again.

The temporary lock period is 15 minutes.

Repeated failed attempts after the account becomes available may cause the account to be locked again.

## How to Recover a Locked Account

Follow these steps:

1. Stop attempting to log in.
2. Wait 15 minutes.
3. Make sure you are using the correct email address.
4. Verify that you are using the latest password.
5. Try logging in again.
6. If you do not remember your password, use the "Forgot Password" option.
7. If the account remains inaccessible, contact your CloudDesk administrator.

## Troubleshooting

| Problem | Cause | Solution |
|---|---|---|
| Account locked after failed attempts | Too many incorrect passwords | Wait 15 minutes before trying again |
| Account still locked after waiting | Additional failed attempts occurred | Stop login attempts and wait another 15 minutes |
| Login fails after unlocking | Incorrect or outdated password | Reset the password using "Forgot Password" |
| Password manager keeps failing | Saved password is outdated | Update the saved CloudDesk password |
| Account remains inaccessible | Administrative restriction | Contact the CloudDesk administrator |
| User does not receive reset email | Email delivery issue | Check spam and verify the registered email address |

## Password Manager Considerations

If you recently changed your password, your browser or password manager may continue submitting the previous password.

If this happens:

1. Open the password manager.
2. Locate the CloudDesk login entry.
3. Update the stored password.
4. Return to CloudDesk.
5. Try logging in again.

## Administrator Assistance

A CloudDesk administrator may need to assist if an account remains inaccessible after the temporary lock period.

When contacting an administrator, provide:

- Your CloudDesk username or registered email.
- The approximate time the issue occurred.
- The error message displayed by CloudDesk.

Do not send your password to the administrator.

## Frequently Asked Questions

### How long does an account remain locked?

A temporary CloudDesk account lock lasts 15 minutes.

### Can I continue trying to log in during the lock?

No. Additional login attempts should be avoided until the temporary lock period expires.

### What if I forgot my password?

Use the "Forgot Password" option to create a new password.

### Does changing the password immediately unlock the account?

If the account is still within the temporary lock period, wait until the lock period expires before attempting to log in again.

## Security Information

CloudDesk does not ask users to provide their passwords through email or support tickets.

If you suspect unauthorized access, reset your password and notify your CloudDesk administrator.
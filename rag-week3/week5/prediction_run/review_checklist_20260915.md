# Week 5 prediction — human review checklist (seed 20260915)

Generated 2026-10-12 from `sample_20260915.json`, the two arm logs under
`traces/week5_prediction/`, and the chunk text in Postgres. **No label in this
file is a decision.** Each trace needs two human decisions, entered in
`notes_20260915.md` (not here):

- `answerable:` exactly `yes`, `no` or `unsure` — do the articles contain the answer?
  The corpus now has seven articles (HC-001–HC-006 and HC-008); decide against what
  the articles say, not against what the system did.
- `modes:` any of `M1 M2 M3 M4 M5`, `NEW:<short-name>`, or `none`.
- `**Observation:**` one sentence of what you saw (not required by `score`, required by the protocol).

Mode definitions (week5/taxonomy.md): M1 says 'not enough information' to a question
the articles do answer · M2 a message that is not one direct question gets a flat
refusal · M3 the reply stops mid-sentence · M4 the reply contains text that is not
an answer · M5 answers 'what should I do' without giving the steps.

Code the **0.85** trace. The 0.75 twin is context. Where the same question was
coded on 2026-09-08 (`week5/notes.md`), that observation is quoted for context: it
described a different response on an older build (`efd07ba`), so it is not a label
for this trace.

Then run `python experiments/week5_prediction.py score`. It refuses (exit 2, no
result file) until all 20 have both `answerable` and `modes`.

---

## 1. `d3e783f2cf6a4a66` — position 73

- **Question:** notifications stopped working yesterday for the whole team
- **Source kind / retrieval:** messy · `dense` · k=7
- **0.85 outcome:** refused:model_reported_not_in_sources · best distance 0.6331
- **0.75 twin `84a0955122e5435d`:** refused:model_reported_not_in_sources · best distance 0.6331
- **Answer shown (0.85):** I don't have enough information to answer that.
- **Raw model output (0.85):**

      NOT_IN_SOURCES

- **Output tokens:** 4 of 160
- **Same question coded 2026-09-08** (`f1423117df0949ac`, context only): "notifications stopped working yesterday for the whole team" retrieved three notification-troubleshooting chunks between 0.63 and 0.75, and the model replied `NOT_IN_SOURCES`.

Retrieved evidence (0.85, in rank order):

- `HC-005-markdown-16`: Notification Settings and Troubleshooting - Notification Troubleshooting | Problem | Possible Cause | Solution | |---|---|---| | Notifications stopped suddenly | Organization settings changed | Contact the CloudDesk administrator |
- `HC-005-markdown-14`: Notification Settings and Troubleshooting - Notification Troubleshooting | Problem | Possible Cause | Solution | |---|---|---| | Only some notifications appear | Notification category disabled | Enable the required notification category |
- `HC-005-markdown-13`: Notification Settings and Troubleshooting - Notification Troubleshooting | Problem | Possible Cause | Solution | |---|---|---| | Browser notification not displayed | Browser permission disabled | Allow CloudDesk notifications in browser settings |

**Decisions still needed:** `answerable:` (yes / no / unsure) · `modes:` (M1–M5, NEW:<name>, or none) · one-sentence observation.

## 2. `c1b1628faa214b46` — position 62

- **Question:** My card expired and the CloudDesk payment failed. What should I do?
- **Source kind / retrieval:** curated_supported · `hybrid_rerank` · k=3
- **0.85 outcome:** refused:ungrounded_answer · best distance 0.734
- **0.75 twin `661dad47820047ce`:** refused:ungrounded_answer · best distance 0.734
- **Answer shown (0.85):** I don't have enough information to answer that.
- **Raw model output (0.85):**

      To resolve this issue, follow these steps:
      
      1. Check if there is an error message indicating that the payment was declined due to insufficient funds.
      2. Verify that the card has not been locked after attempting to log in.
      3. Reset your password by following the instructions provided in step 4.
      4. Contact your CloudDesk administrator for assistance in recovering the locked account.

- **Output tokens:** 75 of 160
- **Same question coded 2026-09-08** (`59b27d3b9fdd4225`, context only): For an expired card and failed payment, the reply listed steps referring to insufficient funds and a card being locked, neither of which appears in the billing article, and stopped mid-word at "loc"; the user was shown the refusal message.

Retrieved evidence (0.85, in rank order):

- `HC-003-markdown-11`: Billing and Payment Management - Failed Payments When a payment fails, CloudDesk displays the payment status in the Billing section.
- `HC-002-markdown-23`: Account Locked After Failed Login Attempts - Security Information CloudDesk does not ask users to provide their passwords through email or support tickets. If you suspect unauthorized access, reset your password and notify your CloudDesk administrator.
- `HC-002-markdown-9`: Account Locked After Failed Login Attempts - How to Recover a Locked Account 5. Try logging in again. 6. If you do not remember your password, use the "Forgot Password" option. 7. If the account remains inaccessible, contact your CloudDesk administrator.

**Decisions still needed:** `answerable:` (yes / no / unsure) · `modes:` (M1–M5, NEW:<name>, or none) · one-sentence observation.

## 3. `f3a06ef14d694967` — position 23

- **Question:** Account locked. Help.
- **Source kind / retrieval:** messy · `hybrid_rerank` · k=7
- **0.85 outcome:** answered · best distance 0.5308
- **0.75 twin `a37fc0f953cc451c`:** answered · best distance 0.5308
- **Answer shown (0.85):** To recover a locked account, follow these steps:

1. Stop attempting to log in.
2. Wait 15 minutes.
3. Ensure you are using the correct email address.
4. Verify that you are using the latest password.
5. Try logging in again.

If the account remains inaccessible, contact your CloudDesk administrator. [1]
- **Raw model output (0.85):**

      To recover a locked account, follow these steps:
      
      1. Stop attempting to log in.
      2. Wait 15 minutes.
      3. Ensure you are using the correct email address.
      4. Verify that you are using the latest password.
      5. Try logging in again.
      
      If the account remains inaccessible, contact your CloudDesk administrator.

- **Output tokens:** 67 of 160
- **Same question coded 2026-09-08** (`7fe81eaa6cdf466a`, context only): The reply began "Follow these steps:" and then reproduced a source header line — `[2] (article HC-002, file article_02.md, updated 2026-08-05)` — before starting a numbered list that stopped at "2"; the user was shown the refusal message.

Retrieved evidence (0.85, in rank order):

- `HC-002-markdown-18`: Account Locked After Failed Login Attempts - Administrator Assistance A CloudDesk administrator may need to assist if an account remains inaccessible after the temporary lock period. When contacting an administrator, provide:
- `HC-002-markdown-1`: Account Locked After Failed Login Attempts - Overview CloudDesk temporarily locks an account when multiple unsuccessful login attempts are detected. This security feature helps protect accounts from repeated unauthorized login attempts.
- `HC-002-markdown-19`: Account Locked After Failed Login Attempts - Administrator Assistance When contacting an administrator, provide: - Your CloudDesk username or registered email. - The approximate time the issue occurred. - The error message displayed by CloudDesk. Do not send your password to the administrator.

**Decisions still needed:** `answerable:` (yes / no / unsure) · `modes:` (M1–M5, NEW:<name>, or none) · one-sentence observation.

## 4. `1bb90578319c42ad` — position 42

- **Question:** My card expired and the CloudDesk payment failed. What should I do?
- **Source kind / retrieval:** curated_supported · `hybrid_rerank` · k=5
- **0.85 outcome:** answered · best distance 0.734
- **0.75 twin `db982d4b22414c8b`:** answered · best distance 0.734
- **Answer shown (0.85):** Try logging in again. If that doesn't work, try resetting your password and notifying your CloudDesk administrator. [1]
- **Raw model output (0.85):**

      Try logging in again. If that doesn't work, try resetting your password and notifying your CloudDesk administrator.

- **Output tokens:** 22 of 160
- **Same question coded 2026-09-08** (`59b27d3b9fdd4225`, context only): For an expired card and failed payment, the reply listed steps referring to insufficient funds and a card being locked, neither of which appears in the billing article, and stopped mid-word at "loc"; the user was shown the refusal message.

Retrieved evidence (0.85, in rank order):

- `HC-003-markdown-11`: Billing and Payment Management - Failed Payments When a payment fails, CloudDesk displays the payment status in the Billing section.
- `HC-002-markdown-23`: Account Locked After Failed Login Attempts - Security Information CloudDesk does not ask users to provide their passwords through email or support tickets. If you suspect unauthorized access, reset your password and notify your CloudDesk administrator.
- `HC-002-markdown-9`: Account Locked After Failed Login Attempts - How to Recover a Locked Account 5. Try logging in again. 6. If you do not remember your password, use the "Forgot Password" option. 7. If the account remains inaccessible, contact your CloudDesk administrator.

**Decisions still needed:** `answerable:` (yes / no / unsure) · `modes:` (M1–M5, NEW:<name>, or none) · one-sentence observation.

## 5. `3d5810c618474120` — position 78

- **Question:** What is the troubleshooting solution when the reset link has expired?
- **Source kind / retrieval:** golden · `hybrid` · k=5
- **0.85 outcome:** answered · best distance 0.567
- **0.75 twin `db89539ca0bc490e`:** answered · best distance 0.567
- **Answer shown (0.85):** Reset link has expired | Request a new password reset link from the login page. [1] [2] [3]
- **Raw model output (0.85):**

      Reset link has expired | Request a new password reset link from the login page.

- **Output tokens:** 16 of 160

Retrieved evidence (0.85, in rank order):

- `HC-001-markdown-11`: Password Reset - Troubleshooting | Problem | Possible Cause | Solution | |---|---|---| | Reset link has expired | Link is older than 30 minutes | Request a new password reset link | | Reset link is invalid | A newer reset link was generated | Use the latest reset email |
- `HC-001-markdown-17`: Password Reset - Frequently Asked Questions ### What should I do if my reset link expired? Request a new password reset link from the login page.
- `HC-001-markdown-16`: Password Reset - Frequently Asked Questions ### Can support reset my password? Support representatives cannot directly provide or reveal your password. Use the password reset process from the login page. ### What should I do if my reset link expired?

**Decisions still needed:** `answerable:` (yes / no / unsure) · `modes:` (M1–M5, NEW:<name>, or none) · one-sentence observation.

## 6. `71cad97adff34c97` — position 18

- **Question:** How much does the CloudDesk Enterprise plan cost per user?
- **Source kind / retrieval:** curated_unsupported · `hybrid` · k=3
- **0.85 outcome:** refused:distance_gate · best distance 1.0468
- **0.75 twin `4c1272bd8cdd4c24`:** refused:distance_gate · best distance 1.0468
- **Answer shown (0.85):** I don't have enough information to answer that.
- **Raw model output (0.85):**

      —

- **Output tokens:** None of 160
- **Same question coded 2026-09-08** (`0397331973e145a3`, context only): A question about Enterprise plan pricing was refused with the nearest chunk at 1.2601; no article covers pricing.

Retrieved evidence (0.85, in rank order):

- `HC-003-markdown-1`: Billing and Payment Management - Overview CloudDesk provides billing management features for organizations with paid subscriptions. Administrators can view invoices, manage payment methods, and review payment status from the Billing section.
- `HC-002-markdown-15`: Account Locked After Failed Login Attempts - Troubleshooting | Problem | Cause | Solution | |---|---|---| | User does not receive reset email | Email delivery issue | Check spam and verify the registered email address |
- `HC-008-markdown-1`: Refunds, Support Tiers and Escalation - Overview This article describes when a CloudDesk subscription charge can be refunded, how refunds are returned, and how tickets from Priority support customers are escalated.

**Decisions still needed:** `answerable:` (yes / no / unsure) · `modes:` (M1–M5, NEW:<name>, or none) · one-sentence observation.

## 7. `03f73bb5a1094519` — position 67

- **Question:** How long do I have to wait?
- **Source kind / retrieval:** curated_ambiguous · `hybrid_rerank` · k=5
- **0.85 outcome:** refused:model_reported_not_in_sources · best distance 1.3271
- **0.75 twin `e2e4f068fcde45d7`:** refused:model_reported_not_in_sources · best distance 1.3271
- **Answer shown (0.85):** I don't have enough information to answer that.
- **Raw model output (0.85):**

      Not_in_sources

- **Output tokens:** 3 of 160

Retrieved evidence (0.85, in rank order):

- `HC-008-markdown-13`: Refunds, Support Tiers and Escalation - Frequently Asked Questions ### Can I get a refund after 30 days? No. A charge older than 30 days is not eligible for a refund. Duplicate charges are the only exception. ### How long does a refund take?
- `HC-008-markdown-14`: Refunds, Support Tiers and Escalation - Frequently Asked Questions ### How long does a refund take? A refund reaches the original payment method within 5 to 10 business days. ### Who can request a refund? Only users with billing permissions can request a refund.
- `HC-008-markdown-3`: Refunds, Support Tiers and Escalation - Refund Eligibility Window A charge made more than 30 days before the refund request is not eligible for a refund. Support agents must not promise or process a refund for a charge outside the 30-day window.

**Decisions still needed:** `answerable:` (yes / no / unsure) · `modes:` (M1–M5, NEW:<name>, or none) · one-sentence observation.

## 8. `88dd7ec000424988` — position 8

- **Question:** What are the limits?
- **Source kind / retrieval:** curated_ambiguous · `hybrid` · k=5
- **0.85 outcome:** refused:model_reported_not_in_sources · best distance 1.1185
- **0.75 twin `9775cc843e4e4912`:** refused:model_reported_not_in_sources · best distance 1.1185
- **Answer shown (0.85):** I don't have enough information to answer that.
- **Raw model output (0.85):**

      NOT_IN_SOURCES

- **Output tokens:** 4 of 160

Retrieved evidence (0.85, in rank order):

- `HC-006-markdown-26`: File Uploads and Storage - Storage and Organization Limits Storage limits may be configured at the organization level.
- `HC-006-markdown-27`: File Uploads and Storage - Storage and Organization Limits If the organization reaches its available storage capacity, administrators may need to review existing files or increase available storage according to the organization's CloudDesk configuration.
- `HC-006-markdown-22`: File Uploads and Storage - Frequently Asked Questions ### Why was my file rejected? The file may use an unsupported format or exceed the configured file size limit. ### What should I do if the file is too large?

**Decisions still needed:** `answerable:` (yes / no / unsure) · `modes:` (M1–M5, NEW:<name>, or none) · one-sentence observation.

## 9. `633409505c0b4c6a` — position 25

- **Question:** How long does a CloudDesk account stay locked after too many failed login attempts?
- **Source kind / retrieval:** curated_supported · `hybrid` · k=7
- **0.85 outcome:** answered · best distance 0.2947
- **0.75 twin `cae2d313a5d84cc7`:** answered · best distance 0.2947
- **Answer shown (0.85):** A temporary CloudDesk account lock lasts 15 minutes. [1] [2]
- **Raw model output (0.85):**

      A temporary CloudDesk account lock lasts 15 minutes.

- **Output tokens:** 12 of 160
- **Same question coded 2026-09-08** (`99bd69c5501749e4`, context only): The account-lock-duration question returned "A temporary CloudDesk account lock lasts 15 minutes." with one citation.

Retrieved evidence (0.85, in rank order):

- `HC-002-markdown-20`: Account Locked After Failed Login Attempts - Frequently Asked Questions ### How long does an account remain locked? A temporary CloudDesk account lock lasts 15 minutes. ### Can I continue trying to log in during the lock?
- `HC-002-markdown-1`: Account Locked After Failed Login Attempts - Overview CloudDesk temporarily locks an account when multiple unsuccessful login attempts are detected. This security feature helps protect accounts from repeated unauthorized login attempts.
- `HC-002-markdown-5`: Account Locked After Failed Login Attempts - What Happens When an Account Is Locked When an account is temporarily locked, CloudDesk prevents additional login attempts for a short period. The user should wait for the temporary lock period to expire before trying again.

**Decisions still needed:** `answerable:` (yes / no / unsure) · `modes:` (M1–M5, NEW:<name>, or none) · one-sentence observation.

## 10. `2577a4d1230243b5` — position 27

- **Question:** How long do I have to wait?
- **Source kind / retrieval:** curated_ambiguous · `dense` · k=5
- **0.85 outcome:** refused:distance_gate · best distance 1.3137
- **0.75 twin `cdc07cbdfdff4ace`:** refused:distance_gate · best distance 1.3137
- **Answer shown (0.85):** I don't have enough information to answer that.
- **Raw model output (0.85):**

      —

- **Output tokens:** None of 160

Retrieved evidence (0.85, in rank order):

- `HC-008-markdown-10`: Refunds, Support Tiers and Escalation - Support Tiers CloudDesk offers two support tiers: Standard and Priority. Standard tickets receive a first response within 1 business day. Priority tickets receive a first response within 1 hour.
- `HC-008-markdown-3`: Refunds, Support Tiers and Escalation - Refund Eligibility Window A charge made more than 30 days before the refund request is not eligible for a refund. Support agents must not promise or process a refund for a charge outside the 30-day window.
- `HC-006-markdown-8`: File Uploads and Storage - Upload Progress Large files may take longer to upload depending on: - File size. - Internet connection speed. - Network stability. - Current CloudDesk service availability. Wait until the upload completes before navigating away from the page.

**Decisions still needed:** `answerable:` (yes / no / unsure) · `modes:` (M1–M5, NEW:<name>, or none) · one-sentence observation.

## 11. `ba0b35cd475747a7` — position 59

- **Question:** What are the password requirements for a new CloudDesk password?
- **Source kind / retrieval:** curated_supported · `dense` · k=5
- **0.85 outcome:** answered · best distance 0.2739
- **0.75 twin `de28fc87dbcc4224`:** answered · best distance 0.2739
- **Answer shown (0.85):** The password requirements for a new CloudDesk password are:

- At least 8 characters.
- At least one uppercase letter.
- At least one lowercase letter.
- At least one number.
- Not be the same as the previous password. [1]
- **Raw model output (0.85):**

      The password requirements for a new CloudDesk password are:
      
      - At least 8 characters.
      - At least one uppercase letter.
      - At least one lowercase letter.
      - At least one number.
      - Not be the same as the previous password.

- **Output tokens:** 48 of 160

Retrieved evidence (0.85, in rank order):

- `HC-001-markdown-6`: Password Reset - Password Requirements A new CloudDesk password must: - Contain at least 8 characters. - Contain at least one uppercase letter. - Contain at least one lowercase letter. - Contain at least one number. - Not be the same as the previous password.
- `HC-001-markdown-2`: Password Reset - How to Reset Your Password Follow these steps to reset your CloudDesk password:
- `HC-001-markdown-1`: Password Reset - Overview CloudDesk users can reset their password if they forget it or if they want to change it for security reasons. A password reset can be performed from the CloudDesk login page without contacting support.

**Decisions still needed:** `answerable:` (yes / no / unsure) · `modes:` (M1–M5, NEW:<name>, or none) · one-sentence observation.

## 12. `9d6fc2a52c1741c3` — position 32

- **Question:** Who can manage organization-level notification controls in HC-005?
- **Source kind / retrieval:** golden · `hybrid` · k=5
- **0.85 outcome:** answered · best distance 0.875
- **0.75 twin `7fcd275284a54a9e`:** answered · best distance 0.875
- **Answer shown (0.85):** Organization Administrator [1] [2]
- **Raw model output (0.85):**

      Organization Administrator

- **Output tokens:** 2 of 160

Retrieved evidence (0.85, in rank order):

- `HC-005-markdown-23`: Notification Settings and Troubleshooting - Organization-Level Notification Controls Some notification settings may be controlled by the CloudDesk organization administrator. If a notification option cannot be changed by an individual user, contact the organization administrator.
- `HC-005-markdown-4`: Notification Settings and Troubleshooting - Managing Notification Preferences To manage notification preferences:
- `HC-005-markdown-26`: Notification Settings and Troubleshooting - Frequently Asked Questions ### Can users change all notification settings? Not necessarily. Some notification settings may be controlled by the organization administrator. ### What should I do if only one type of notification is missing?

**Decisions still needed:** `answerable:` (yes / no / unsure) · `modes:` (M1–M5, NEW:<name>, or none) · one-sentence observation.

## 13. `fdca2cebc2e84b6b` — position 11

- **Question:** I need to filter tickets but I don't know where that setting is
- **Source kind / retrieval:** messy · `dense` · k=5
- **0.85 outcome:** refused:model_reported_not_in_sources · best distance 0.6812
- **0.75 twin `37183df9d9d44fe8`:** refused:model_reported_not_in_sources · best distance 0.6812
- **Answer shown (0.85):** I don't have enough information to answer that.
- **Raw model output (0.85):**

      NOT_IN_SOURCES

- **Output tokens:** 4 of 160

Retrieved evidence (0.85, in rank order):

- `HC-004-markdown-4`: Ticket Search and Filtering - Filtering Tickets Filters can be used to narrow the list of tickets. Common filters include: - Status - Priority - Assignee - Ticket type - Creation date - Updated date For example, you can filter tickets to show only open tickets assigned to a specific user.
- `HC-004-markdown-8`: Ticket Search and Filtering - Filtering by Priority To filter by priority: 1. Open the Tickets section. 2. Select the Priority filter. 3. Choose the required priority. 4. Apply the filter.
- `HC-004-markdown-6`: Ticket Search and Filtering - Filtering by Status To filter by status: 1. Open the Tickets section. 2. Select the Status filter. 3. Select the required status. 4. Apply the filter. 5. Review the filtered results.

**Decisions still needed:** `answerable:` (yes / no / unsure) · `modes:` (M1–M5, NEW:<name>, or none) · one-sentence observation.

## 14. `2ea18980635f423a` — position 45

- **Question:** Does CloudDesk integrate with Salesforce?
- **Source kind / retrieval:** curated_unsupported · `dense` · k=5
- **0.85 outcome:** refused:model_reported_not_in_sources · best distance 0.7744
- **0.75 twin `944bf213e147403b`:** refused:distance_gate · best distance 0.7744 · **threshold changed the outcome: refused:distance_gate -> refused:model_reported_not_in_sources**
- **Answer shown (0.85):** I don't have enough information to answer that.
- **Raw model output (0.85):**

      NOT_IN_SOURCES

- **Output tokens:** 4 of 160
- **Same question coded 2026-09-08** (`481c82d2b24a4c26`, context only): The Salesforce question again, in a different retrieval mode, refused at the same 0.7744 with a different second and third result.

Retrieved evidence (0.85, in rank order):

- `HC-003-markdown-1`: Billing and Payment Management - Overview CloudDesk provides billing management features for organizations with paid subscriptions. Administrators can view invoices, manage payment methods, and review payment status from the Billing section.
- `HC-006-markdown-1`: File Uploads and Storage - Overview CloudDesk allows users to upload files to tickets and other supported areas of the application. Uploaded files may be subject to file type, file size, and organization-level restrictions.
- `HC-003-markdown-4`: Billing and Payment Management - Supported Payment Methods CloudDesk supports the payment methods configured for your organization's subscription. The available payment methods are displayed in the Billing section when adding or updating a payment method.

**Decisions still needed:** `answerable:` (yes / no / unsure) · `modes:` (M1–M5, NEW:<name>, or none) · one-sentence observation.

## 15. `076d42c7a5674fb4` — position 75

- **Question:** What should I do if an account is locked due to incorrect password attempts in HC-002?
- **Source kind / retrieval:** golden · `dense` · k=7
- **0.85 outcome:** refused:ungrounded_answer · best distance 0.5702
- **0.75 twin `e66ae9255bcd456e`:** refused:ungrounded_answer · best distance 0.5702
- **Answer shown (0.85):** I don't have enough information to answer that.
- **Raw model output (0.85):**

      To recover a locked account, follow these steps:
      
      [2] (article HC-002, file article_02.md, updated 2026-08-05)
      
      1. Stop attempting to log in.
      2. Wait 15 minutes.
      3. Make sure you are using the correct email address.
      4. Verify that you are using the latest password.
      5. Try logging in again.

- **Output tokens:** 87 of 160
- **Same question coded 2026-09-08** (`52df90703dac44be`, context only): Asked what to do about an account locked by incorrect password attempts, the reply said attempts should be avoided until the lock expires and listed no recovery action, citing one chunk.

Retrieved evidence (0.85, in rank order):

- `HC-002-markdown-7`: Account Locked After Failed Login Attempts - How to Recover a Locked Account Follow these steps:
- `HC-002-markdown-4`: Account Locked After Failed Login Attempts - Why an Account Becomes Locked - Attempting to log in from an application that has stored an outdated password.
- `HC-002-markdown-3`: Account Locked After Failed Login Attempts - Why an Account Becomes Locked - Entering an incorrect password multiple times. - Using an old password after changing the password. - Using saved credentials from a password manager.

**Decisions still needed:** `answerable:` (yes / no / unsure) · `modes:` (M1–M5, NEW:<name>, or none) · one-sentence observation.

## 16. `105c30e831d549ef` — position 71

- **Question:** Is there a way to export invoices?
- **Source kind / retrieval:** messy · `hybrid_rerank` · k=5
- **0.85 outcome:** refused:model_reported_not_in_sources · best distance 1.0126
- **0.75 twin `791b320d59d9478c`:** refused:model_reported_not_in_sources · best distance 1.0126
- **Answer shown (0.85):** I don't have enough information to answer that.
- **Raw model output (0.85):**

      NOT_IN_SOURCES

- **Output tokens:** 4 of 160

Retrieved evidence (0.85, in rank order):

- `HC-003-markdown-16`: Billing and Payment Management - Troubleshooting | Problem | Possible Cause | Solution | |---|---|---| | Invoice is not visible | User lacks billing permissions | Contact an organization administrator |
- `HC-008-markdown-8`: Refunds, Support Tiers and Escalation - How to Request a Refund To request a refund: 1. Open Organization Settings. 2. Select "Billing". 3. Select "Invoices". 4. Choose the invoice for the charge. 5. Select "Request Refund". 6. Enter the reason for the request and submit it.
- `HC-003-markdown-18`: Billing and Payment Management - Invoice Management Billing administrators can view available invoices from: ```text Organization Settings ↓ Billing ↓ Invoices

**Decisions still needed:** `answerable:` (yes / no / unsure) · `modes:` (M1–M5, NEW:<name>, or none) · one-sentence observation.

## 17. `1cf08481fc1e4cdc` — position 34

- **Question:** What should I do if my CloudDesk password reset link has expired?
- **Source kind / retrieval:** curated_supported · `hybrid` · k=3
- **0.85 outcome:** answered · best distance 0.4741
- **0.75 twin `a42168c4db8e4f83`:** answered · best distance 0.4741
- **Answer shown (0.85):** Reset your password immediately and contact your organization's CloudDesk administrator. [1]
- **Raw model output (0.85):**

      Reset your password immediately and contact your organization's CloudDesk administrator.

- **Output tokens:** 13 of 160

Retrieved evidence (0.85, in rank order):

- `HC-001-markdown-15`: Password Reset - Frequently Asked Questions ### How long is a password reset link valid? A password reset link is valid for 30 minutes. ### Can I reuse my previous password? No. CloudDesk does not allow the previous password to be reused. ### Can support reset my password?
- `HC-001-markdown-14`: Password Reset - Security Information CloudDesk support representatives cannot provide or reveal a user's existing password. If you believe someone else has access to your account, reset your password immediately and contact your organization's CloudDesk administrator.
- `HC-001-markdown-17`: Password Reset - Frequently Asked Questions ### What should I do if my reset link expired? Request a new password reset link from the login page.

**Decisions still needed:** `answerable:` (yes / no / unsure) · `modes:` (M1–M5, NEW:<name>, or none) · one-sentence observation.

## 18. `2bcca536e9874c54` — position 33

- **Question:** Why is the Upload File button unavailable in CloudDesk?
- **Source kind / retrieval:** curated_supported · `hybrid_rerank` · k=3
- **0.85 outcome:** answered · best distance 0.3716
- **0.75 twin `d918484939fb47a4`:** answered · best distance 0.3716
- **Answer shown (0.85):** The Upload File button is unavailable in CloudDesk because some areas may restrict who can upload or access files. If this feature is unavailable, the user's role may not have the required permission. The organization administrator should contact them to verify the necessary permissions. [1]
- **Raw model output (0.85):**

      The Upload File button is unavailable in CloudDesk because some areas may restrict who can upload or access files. If this feature is unavailable, the user's role may not have the required permission. The organization administrator should contact them to verify the necessary permissions.

- **Output tokens:** 50 of 160

Retrieved evidence (0.85, in rank order):

- `HC-006-markdown-20`: File Uploads and Storage - File Permissions Some CloudDesk areas may restrict who can upload or access files. If the Upload File option is unavailable, the user's role may not have the required permission. Contact the organization administrator to verify the required permissions.
- `HC-006-markdown-11`: File Uploads and Storage - File Upload Troubleshooting | Problem | Possible Cause | Solution | |---|---|---| | Upload button unavailable | User lacks required permission | Contact the CloudDesk administrator |
- `HC-006-markdown-3`: File Uploads and Storage - File Size Limit Individual files must not exceed the maximum file size configured for the CloudDesk organization. If a file exceeds the configured limit, the upload will fail.

**Decisions still needed:** `answerable:` (yes / no / unsure) · `modes:` (M1–M5, NEW:<name>, or none) · one-sentence observation.

## 19. `358d8c7920fd4040` — position 48

- **Question:** Why is the Upload File button unavailable according to HC-006 troubleshooting?
- **Source kind / retrieval:** golden · `hybrid_rerank` · k=3
- **0.85 outcome:** refused:ungrounded_answer · best distance 0.7596
- **0.75 twin `976c2269648642eb`:** refused:ungrounded_answer · best distance 0.7596
- **Answer shown (0.85):** I don't have enough information to answer that.
- **Raw model output (0.85):**

      The Upload File button is unavailable according to HC-006 troubleshooting because users lack required permission.

- **Output tokens:** 20 of 160
- **Same question coded 2026-09-08** (`55dc68f2276e4924`, context only): The model wrote "The Upload File button is unavailable according to HC-006 troubleshooting because users lack required permission" and the user was shown the refusal message instead, with no citation.

Retrieved evidence (0.85, in rank order):

- `HC-006-markdown-11`: File Uploads and Storage - File Upload Troubleshooting | Problem | Possible Cause | Solution | |---|---|---| | Upload button unavailable | User lacks required permission | Contact the CloudDesk administrator |
- `HC-006-markdown-14`: File Uploads and Storage - Troubleshooting Large Files If an upload fails because the file is too large:
- `HC-006-markdown-20`: File Uploads and Storage - File Permissions Some CloudDesk areas may restrict who can upload or access files. If the Upload File option is unavailable, the user's role may not have the required permission. Contact the organization administrator to verify the required permissions.

**Decisions still needed:** `answerable:` (yes / no / unsure) · `modes:` (M1–M5, NEW:<name>, or none) · one-sentence observation.

## 20. `5113e766fcb547f8` — position 83

- **Question:** How long is a password reset link valid in CloudDesk?
- **Source kind / retrieval:** golden · `dense` · k=7
- **0.85 outcome:** answered · best distance 0.3644
- **0.75 twin `ca14c02cba38401e`:** answered · best distance 0.3644
- **Answer shown (0.85):** A password reset link is valid for 30 minutes. [1]
- **Raw model output (0.85):**

      A password reset link is valid for 30 minutes.

- **Output tokens:** 12 of 160

Retrieved evidence (0.85, in rank order):

- `HC-001-markdown-15`: Password Reset - Frequently Asked Questions ### How long is a password reset link valid? A password reset link is valid for 30 minutes. ### Can I reuse my previous password? No. CloudDesk does not allow the previous password to be reused. ### Can support reset my password?
- `HC-001-markdown-6`: Password Reset - Password Requirements A new CloudDesk password must: - Contain at least 8 characters. - Contain at least one uppercase letter. - Contain at least one lowercase letter. - Contain at least one number. - Not be the same as the previous password.
- `HC-001-markdown-18`: Password Reset - Related Information Password reset issues are part of the CloudDesk Authentication product area.

**Decisions still needed:** `answerable:` (yes / no / unsure) · `modes:` (M1–M5, NEW:<name>, or none) · one-sentence observation.


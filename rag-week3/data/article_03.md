---
article_id: HC-003
product_area: Billing
last_updated: 2026-08-03
---

# Billing and Payment Management

## Overview

CloudDesk provides billing management features for organizations with paid subscriptions.

Administrators can view invoices, manage payment methods, and review payment status from the Billing section.

## Viewing Billing Information

To view billing information:

1. Sign in to CloudDesk.
2. Open the organization settings.
3. Select "Billing".
4. Review the current subscription information.
5. Select "Invoices" to view previous invoices.

Only users with appropriate billing permissions can access billing information.

## Supported Payment Methods

CloudDesk supports the payment methods configured for your organization's subscription.

The available payment methods are displayed in the Billing section when adding or updating a payment method.

A billing administrator should verify the available payment methods before attempting a payment.

## Adding a Payment Method

To add a payment method:

1. Open CloudDesk.
2. Navigate to Organization Settings.
3. Select "Billing".
4. Open "Payment Methods".
5. Select "Add Payment Method".
6. Enter the requested payment information.
7. Save the payment method.

The newly added payment method can be selected for future billing transactions.

## Updating a Payment Method

To update payment information:

1. Open Organization Settings.
2. Select "Billing".
3. Select "Payment Methods".
4. Select the payment method you want to update.
5. Enter the new information.
6. Save the changes.

If a payment method has expired, update it before the next billing attempt.

## Failed Payments

A payment may fail because of:

- An expired payment method.
- Incorrect payment information.
- A payment provider decline.
- Insufficient available funds.
- A temporary payment processing problem.

When a payment fails, CloudDesk displays the payment status in the Billing section.

## Troubleshooting

| Problem | Possible Cause | Solution |
|---|---|---|
| Payment failed | Payment provider declined the transaction | Verify payment information or contact the payment provider |
| Card expired | Payment method is no longer valid | Update the payment method |
| Incorrect billing information | Billing details do not match payment information | Review and update billing details |
| Payment repeatedly fails | Payment provider is rejecting transactions | Contact the payment provider |
| Invoice is not visible | User lacks billing permissions | Contact an organization administrator |
| Payment status is pending | Payment is still being processed | Wait for the payment status to update |

## Invoice Management

Billing administrators can view available invoices from:

```text
Organization Settings
        ↓
Billing
        ↓
Invoices
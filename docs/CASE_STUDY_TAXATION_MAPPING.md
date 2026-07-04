# Case Study: ProfitBeforeTax and Taxation Mapping

Some Pakistani financial statements include a levy presentation such as:

```text
Profit before levies and taxation
Minimum tax
Final taxes
Loss / profit before taxation
Taxation
Profit for the year
```

A simple keyword matcher can incorrectly select the first line for both ProfitBeforeTax and Taxation because it contains the word "taxation".

The implemented rule is:

- Taxation must not match rows whose label contains profit/loss/before-tax wording.
- ProfitBeforeTax should prefer the real profit/loss-before-tax row immediately above the Taxation row.
- The rough ProfitBeforeTax walk-down is advisory only because real statements can include extra items not represented in the simplified table.

This rule is generic and is covered by focused unit tests.

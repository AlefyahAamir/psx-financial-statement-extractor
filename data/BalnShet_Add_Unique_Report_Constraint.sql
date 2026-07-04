USE PSXFinancials;
GO

/*
Non-destructive DB integrity script.

Use this only if your existing dbo.BalnShet table already has the cleaned column names used by this project.
If you are still testing and can delete old saved rows, prefer:
    data\PSXFinancials_Setup_Recreate_Table.sql

Notes:
- SQL Server cannot convert an existing int column into IDENTITY in-place.
- This script adds the unique report constraint only.
- To get TransactionNumber as IDENTITY PRIMARY KEY, recreate the table using the setup script.
*/

IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = 'UQ_BalnShet_Report'
      AND object_id = OBJECT_ID('dbo.BalnShet')
)
BEGIN
    ALTER TABLE dbo.BalnShet
    ADD CONSTRAINT UQ_BalnShet_Report UNIQUE (Symbol, FinancialYear, PeriodEndDate, ReportType);
END
GO

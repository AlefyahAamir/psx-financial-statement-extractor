USE PSXFinancials;
GO

IF OBJECT_ID('dbo.BalnShet', 'U') IS NULL
BEGIN
    RAISERROR('dbo.BalnShet does not exist. Run PSXFinancials_Setup_Recreate_Table.sql first.', 16, 1);
    RETURN;
END
GO

IF COL_LENGTH('dbo.BalnShet', 'Symbol') IS NULL ALTER TABLE dbo.BalnShet ADD [Symbol] [nvarchar](30) NULL;
IF COL_LENGTH('dbo.BalnShet', 'ReportType') IS NULL ALTER TABLE dbo.BalnShet ADD [ReportType] [nvarchar](50) NULL;
IF COL_LENGTH('dbo.BalnShet', 'PdfUrl') IS NULL ALTER TABLE dbo.BalnShet ADD [PdfUrl] [nvarchar](1000) NULL;
IF COL_LENGTH('dbo.BalnShet', 'ExtractionStatus') IS NULL ALTER TABLE dbo.BalnShet ADD [ExtractionStatus] [nvarchar](50) NULL;
GO

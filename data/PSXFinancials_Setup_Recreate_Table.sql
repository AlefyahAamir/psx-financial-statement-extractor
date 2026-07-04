USE master;
GO

IF DB_ID('PSXFinancials') IS NULL
BEGIN
    CREATE DATABASE PSXFinancials;
END
GO

USE PSXFinancials;
GO

DROP TABLE IF EXISTS dbo.BalnShet;
GO

CREATE TABLE [dbo].[BalnShet](
       [TransactionNumber] [int] IDENTITY(1,1) NOT NULL,
       [Symbol] [nvarchar](30) NOT NULL,
       [CompanyCode] [int] NULL,
       [FinancialYear] [smallint] NOT NULL,
       [PeriodEndDate] [smalldatetime] NOT NULL,
       [ReportType] [nvarchar](50) NOT NULL,
       [PdfUrl] [nvarchar](1000) NULL,
       [ExtractionStatus] [nvarchar](50) NULL,
       [PaidUpCapital] [decimal](18, 0) NULL,
       [Reserves] [decimal](18, 0) NULL,
       [UnappropriatedProfit] [decimal](18, 0) NULL,
       [ShareholdersEquity] [decimal](18, 0) NULL,
       [CurrentAssets] [decimal](18, 0) NULL,
       [CashAndBankBalances] [decimal](18, 0) NULL,
       [AdvancesAndReceivables] [decimal](18, 0) NULL,
       [FixedAssets] [decimal](18, 0) NULL,
       [LongTermLiabilities] [decimal](18, 0) NULL,
       [OtherLongTermLiabilities] [decimal](18, 0) NULL,
       [OtherLiabilities] [decimal](18, 0) NULL,
       [WorkingCapital] [decimal](18, 0) NULL,
       [Sales] [decimal](18, 0) NULL,
       [CostOfSales] [decimal](18, 0) NULL,
       [GrossProfit] [decimal](18, 0) NULL,
       [OperatingExpenses] [decimal](18, 0) NULL,
       [FinanceCosts] [decimal](18, 0) NULL,
       [OtherIncome] [decimal](18, 0) NULL,
       [OtherCharges] [decimal](18, 0) NULL,
       [ProfitBeforeTax] [decimal](18, 0) NULL,
       [Taxation] [decimal](18, 0) NULL,
       [ProfitAfterTax] [decimal](18, 0) NULL,
       [RevaluationSurplus] [decimal](18, 0) NULL,
       [CurrentRatio] [char](10) NULL,
       [DebtRatio] [char](10) NULL,
       [BreakupValue] [char](10) NULL,
       [SubordinatedLoans] [decimal](18, 0) NULL,
       [LongTermBorrowings] [decimal](18, 0) NULL,
       [CurrentLiabilities] [decimal](18, 0) NULL,
       [CurrentPortionLongTermLiabilities] [decimal](18, 0) NULL,
       [ShortTermBorrowings] [decimal](18, 0) NULL,
       [TotalBorrowings] [decimal](18, 0) NULL,
       [TradeDebts] [decimal](18, 0) NULL,
       [StockInTrade] [decimal](18, 0) NULL,
       [StoresAndSpares] [decimal](18, 0) NULL,
       [ShortTermInvestments] [decimal](18, 0) NULL,
       [LongTermInvestments] [decimal](18, 0) NULL,
       [OtherFixedAssets] [decimal](18, 0) NULL,
       [LeaseFinance] [decimal](18, 0) NULL,
       [TradeAndOtherPayables] [decimal](18, 0) NULL,
       [CashFlowFromOperatingActivities] [decimal](18, 0) NULL,
       [CashFlowFromFinancingActivities] [decimal](18, 0) NULL,
       [CashFlowFromInvestingActivities] [decimal](18, 0) NULL,
       [DeferredLiabilities] [decimal](18, 0) NULL,
       [FinanceLeaseObligations] [decimal](18, 0) NULL,
       [OperatingLeaseObligations] [decimal](18, 0) NULL,
       [AmountMultiplier] [int] NULL,
       [CurrentLeaseFinance] [decimal](18, 0) NULL,
       [DepreciationProvision] [decimal](18, 0) NULL,
       [OperatingProfit] [decimal](18, 0) NULL,
       CONSTRAINT [PK_BalnShet] PRIMARY KEY CLUSTERED ([TransactionNumber] ASC),
       CONSTRAINT [UQ_BalnShet_Report] UNIQUE ([Symbol], [FinancialYear], [PeriodEndDate], [ReportType])
);
GO

SELECT TOP 100 *
FROM dbo.BalnShet
ORDER BY TransactionNumber DESC;
GO

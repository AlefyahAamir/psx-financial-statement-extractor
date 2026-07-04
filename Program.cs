using System.Diagnostics;
using System.Text;
using System.Text.Json;

var builder = WebApplication.CreateBuilder(args);
builder.Services.ConfigureHttpJsonOptions(options =>
{
    options.SerializerOptions.PropertyNamingPolicy = JsonNamingPolicy.CamelCase;
    options.SerializerOptions.WriteIndented = true;
});

var app = builder.Build();
var root = app.Environment.ContentRootPath;
Directory.CreateDirectory(Path.Combine(root, "App_Data", "jobs"));
Directory.CreateDirectory(Path.Combine(root, "App_Data", "saved"));
Directory.CreateDirectory(Path.Combine(root, "App_Data", "downloads"));

app.UseDefaultFiles();
app.UseStaticFiles();

app.MapGet("/api/health", () => Results.Json(new
{
    ok = true,
    source = "https://financials.psx.com.pk/",
    note = "Python worker for PSX report discovery, OCR/layout extraction, and SQL save."
}));

app.MapGet("/api/fields", () => Results.Json(BalnShetFields.All));

app.MapGet("/api/companies", (string? term) =>
{
    var results = CompanyCache.Search(root, term ?? string.Empty);
    return Results.Json(results);
});

// Report discovery — Python worker (PSX AJAX scraping)
app.MapPost("/api/reports", async (ReportsRequest request) =>
{
    var result = await PythonWorker.RunAsync(app.Configuration, root, "reports", request);
    return Results.Content(result.Json, "application/json", Encoding.UTF8, result.Success ? 200 : 500);
});

// Extraction — Python worker with layout-aware text extraction and local Tesseract OCR fallback.
app.MapPost("/api/extract", async (ExtractRequest request) =>
{
    var result = await PythonWorker.RunAsync(app.Configuration, root, "extract", request);
    return Results.Content(result.Json, "application/json", Encoding.UTF8, result.Success ? 200 : 500);
});

// SQL save — Python worker
app.MapPost("/api/save", async (SaveRequest request) =>
{
    var result = await PythonWorker.RunAsync(app.Configuration, root, "save", request);
    return Results.Content(result.Json, "application/json", Encoding.UTF8, result.Success ? 200 : 500);
});

app.Run();

// =============================================================================
//  Shared request / response models
// =============================================================================

public sealed record CompanyOption(string Symbol, string Name, string? CompCode);
public sealed record ReportsRequest(string Symbol, string CompanyName, int Year, string? ReportType);

// The browser posts: { symbol, companyName, year, compCode, reports: [...] }
// where each report has: { id, reportType, title, periodEnded, published, url, source }.
// Request/response records are defined below in this file.

public sealed record ExtractRequest(
    string Symbol, string CompanyName, int Year, string? CompCode,
    List<ReportOption> Reports);

public sealed record ReportOption(
    string? Id, string? ReportType, string? Title,
    string? PeriodEnded, string? Published, string Url, string? Source);

public sealed record SaveRequest(
    string Symbol, string CompanyName, int Year, string? CompCode,
    List<SavedReport> Reports);

public sealed record SavedReport(
    string? ReportId, string? ReportType, string? Title,
    string? TranDate, string? Published, string? Url,
    string? Status, string? CachedPdfPath, int? FilledFieldCount,
    Dictionary<string, object?> Values, List<string>? Warnings);

public static class BalnShetFields
{
    public static readonly string[] All = new[]
    {
        "FinancialYear", "PeriodEndDate", "PaidUpCapital", "Reserves",
        "UnappropriatedProfit", "ShareholdersEquity", "CurrentAssets", "CashAndBankBalances",
        "AdvancesAndReceivables", "FixedAssets", "LongTermLiabilities", "OtherLongTermLiabilities",
        "OtherLiabilities", "WorkingCapital", "Sales", "CostOfSales",
        "GrossProfit", "OperatingExpenses", "FinanceCosts", "OtherIncome",
        "OtherCharges", "ProfitBeforeTax", "Taxation", "ProfitAfterTax",
        "RevaluationSurplus", "CurrentRatio", "DebtRatio", "BreakupValue",
        "SubordinatedLoans", "LongTermBorrowings", "CurrentLiabilities", "CurrentPortionLongTermLiabilities",
        "ShortTermBorrowings", "TotalBorrowings", "TradeDebts", "StockInTrade",
        "StoresAndSpares", "ShortTermInvestments", "LongTermInvestments", "OtherFixedAssets",
        "LeaseFinance", "TradeAndOtherPayables", "CashFlowFromOperatingActivities", "CashFlowFromFinancingActivities",
        "CashFlowFromInvestingActivities", "DeferredLiabilities", "FinanceLeaseObligations", "OperatingLeaseObligations",
        "AmountMultiplier", "CurrentLeaseFinance", "DepreciationProvision", "OperatingProfit"
    };
}

public static class CompanyCache
{
    public static IReadOnlyList<CompanyOption> Search(string root, string term)
    {
        var path = Path.Combine(root, "data", "companies.csv");
        var normalizedTerm = Normalize(term);
        var list = new List<CompanyOption>();
        if (!File.Exists(path)) return list;
        foreach (var line in File.ReadLines(path).Skip(1))
        {
            if (string.IsNullOrWhiteSpace(line)) continue;
            var parts = SplitCsv(line);
            if (parts.Count < 2) continue;
            var symbol   = parts[0].Trim();
            var name     = parts[1].Trim();
            var compCode = parts.Count > 2 && !string.IsNullOrWhiteSpace(parts[2]) ? parts[2].Trim() : null;
            var haystack = Normalize(symbol + " " + name);
            if (normalizedTerm.Length == 0 || haystack.Contains(normalizedTerm))
                list.Add(new CompanyOption(symbol, name, compCode));
        }
        return list.ToList();
    }
    private static string Normalize(string value) =>
        new string(value.ToLowerInvariant()
            .Where(ch => char.IsLetterOrDigit(ch) || char.IsWhiteSpace(ch)).ToArray()).Trim();
    private static List<string> SplitCsv(string line)
    {
        var result = new List<string>();
        var current = new StringBuilder();
        bool insideQuotes = false;
        foreach (var ch in line)
        {
            if (ch == '"') { insideQuotes = !insideQuotes; continue; }
            if (ch == ',' && !insideQuotes) { result.Add(current.ToString()); current.Clear(); }
            else current.Append(ch);
        }
        result.Add(current.ToString());
        return result;
    }
}

public static class PythonWorker
{
    public static async Task<(bool Success, string Json)> RunAsync(
        IConfiguration config, string root, string command, object payload)
    {
        var jobId     = DateTime.UtcNow.ToString("yyyyMMddHHmmssfff") + "_" + Guid.NewGuid().ToString("N")[..8];
        var jobs      = Path.Combine(root, "App_Data", "jobs");
        Directory.CreateDirectory(jobs);
        var inputPath  = Path.Combine(jobs, jobId + "_input.json");
        var outputPath = Path.Combine(jobs, jobId + "_output.json");

        var json = JsonSerializer.Serialize(payload,
            new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.CamelCase, WriteIndented = true });
        await File.WriteAllTextAsync(inputPath, json, new UTF8Encoding(false));

        var scriptRelative   = config["Python:ExtractorScript"] ?? "workers\\psx_worker.py";
        var scriptPath       = Path.Combine(root, scriptRelative.Replace('\\', Path.DirectorySeparatorChar));
        var configuredPython = config["Python:ExePath"] ?? "python";
        var fallbackPython   = config["Python:FallbackExePath"] ?? "python";
        var pythonPath       = Path.Combine(root, configuredPython.Replace('\\', Path.DirectorySeparatorChar));
        var pythonExe        = File.Exists(pythonPath) ? pythonPath : fallbackPython;
        var timeoutSeconds   = int.TryParse(config["Python:TimeoutSeconds"], out var t) ? t : 300;

        if (!File.Exists(scriptPath))
            return (false, JsonSerializer.Serialize(new { ok = false, error = "Python worker not found", scriptPath }));

        var start = new ProcessStartInfo
        {
            FileName               = pythonExe,
            Arguments              = $"{Q(scriptPath)} {command} --input {Q(inputPath)} --output {Q(outputPath)} --root {Q(root)}",
            WorkingDirectory       = Path.GetDirectoryName(scriptPath) ?? root,
            RedirectStandardOutput = true,
            RedirectStandardError  = true,
            UseShellExecute        = false,
            CreateNoWindow         = true,
        };

        try
        {
            using var process = Process.Start(start);
            if (process == null)
                return (false, JsonSerializer.Serialize(new { ok = false, error = "Could not start Python process", pythonExe }));

            var stdoutTask = process.StandardOutput.ReadToEndAsync();
            var stderrTask = process.StandardError.ReadToEndAsync();
            var finished   = await Task.Run(() => process.WaitForExit(timeoutSeconds * 1000));

            if (!finished)
            {
                try { process.Kill(entireProcessTree: true); } catch { }
                return (false, JsonSerializer.Serialize(new { ok = false, error = "Python worker timed out", timeoutSeconds }));
            }

            await stdoutTask; await stderrTask;
            if (File.Exists(outputPath))
                return (process.ExitCode == 0, await File.ReadAllTextAsync(outputPath, Encoding.UTF8));

            var stdout = await stdoutTask;
            var stderr = await stderrTask;
            return (false, JsonSerializer.Serialize(new
            {
                ok = false, error = "Python worker produced no output JSON",
                exitCode = process.ExitCode, stdout, stderr
            }));
        }
        catch (Exception ex)
        {
            return (false, JsonSerializer.Serialize(new { ok = false, error = ex.Message, pythonExe, command }));
        }
    }
    private static string Q(string v) => "\"" + v.Replace("\"", "\\\"") + "\"";
}

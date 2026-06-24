import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const inputPath = "C:/Users/Administrator/Downloads/【天天练-活动】期末冲刺营（2期）_测试用例.xlsx";
const input = await FileBlob.load(inputPath);
const workbook = await SpreadsheetFile.importXlsx(input);

async function inspect(kind, options = {}) {
  const result = await workbook.inspect({ kind, ...options });
  return result?.ndjson ?? JSON.stringify(result, null, 2);
}

const outputs = [];
for (const [kind, options] of [
  ["workbook", { summary: "workbook overview" }],
  ["table", { range: "A1:Z40", include: "values,formulas", tableMaxRows: 40, tableMaxCols: 26 }],
  ["match", { searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 100 }, summary: "formula errors" }],
]) {
  try {
    outputs.push(`--- ${kind} ---`);
    outputs.push(await inspect(kind, options));
  } catch (error) {
    outputs.push(`--- ${kind} ERROR ---`);
    outputs.push(error?.stack || String(error));
  }
}

console.log(outputs.join("\n"));

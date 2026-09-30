// GET /api/product/operations/reports/daily -> Product API GET
// /api/v1/operations/reports/daily with only store_id and business_date.
import { proxyToProduct } from "../../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: Request): Promise<Response> {
  return proxyToProduct(request, "dailyReport", {
    authorization: true,
    query: ["store_id", "business_date"],
  });
}

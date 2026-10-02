// GET /api/product/integrations/catalog -> Product API GET /api/v1/integrations/catalog.
import { proxyToProduct } from "../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: Request): Promise<Response> {
  return proxyToProduct(request, "integrationsCatalog", { authorization: true });
}

// POST /api/product/integrations/connection/disable?connection_id= -> Product API POST
// /api/v1/integrations/connection/disable.
import { proxyToProduct } from "../../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function POST(request: Request): Promise<Response> {
  return proxyToProduct(request, "integrationDisable", { authorization: true, query: ["connection_id"] });
}

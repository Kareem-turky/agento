// POST /api/product/integrations/connection/test?connection_id= -> Product API POST
// /api/v1/integrations/connection/test.
import { proxyToProduct } from "../../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function POST(request: Request): Promise<Response> {
  return proxyToProduct(request, "integrationTest", { authorization: true, query: ["connection_id"] });
}

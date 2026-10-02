// PUT /api/product/integrations/connection/credentials?connection_id= -> Product API PUT
// /api/v1/integrations/connection/credentials (explicit credential replacement). The
// values pass straight through and are never stored, cached or logged here.
import { proxyToProduct } from "../../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function PUT(request: Request): Promise<Response> {
  return proxyToProduct(request, "integrationCredentials", { authorization: true, query: ["connection_id"], body: true });
}

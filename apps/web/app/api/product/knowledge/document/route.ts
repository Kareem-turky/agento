// GET /api/product/knowledge/document?document_id= -> Product API GET
// /api/v1/knowledge/document (the document, its current text and its version history).
import { proxyToProduct } from "../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: Request): Promise<Response> {
  return proxyToProduct(request, "knowledgeDocument", { authorization: true, query: ["document_id"] });
}

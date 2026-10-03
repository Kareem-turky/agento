// POST /api/product/knowledge/document/archive?document_id= -> Product API POST
// /api/v1/knowledge/document/archive (excluded from retrieval; history kept; no delete).
import { proxyToProduct } from "../../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function POST(request: Request): Promise<Response> {
  return proxyToProduct(request, "knowledgeDocumentArchive", { authorization: true, query: ["document_id"] });
}

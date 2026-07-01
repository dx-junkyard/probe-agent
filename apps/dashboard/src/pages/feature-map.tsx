import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useLatestDrafts, useGenerateDrafts, useCodeLinks, useGenerateCodeLinks, useReviewCodeLink, useCapabilityHierarchy } from "@/api/hooks";
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { toast } from "sonner";
import { Sparkles, Link2, CheckCircle, XCircle, FileText, Boxes } from "lucide-react";
import { AddToWorkspaceButton } from "@/components/add-to-workspace";

export default function FeatureMapPage() {
  const { data: drafts, isLoading: draftsLoading } = useLatestDrafts();
  const generateDrafts = useGenerateDrafts();
  const { data: codeLinks, isLoading: linksLoading } = useCodeLinks();
  const generateLinks = useGenerateCodeLinks();
  const reviewLink = useReviewCodeLink();
  const { data: hierarchy } = useCapabilityHierarchy();
  const [searchParams] = useSearchParams();
  const [highlightedFeature, setHighlightedFeature] = useState<string | null>(null);
  const highlightRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const featureParam = searchParams.get("feature");
    if (featureParam) {
      setHighlightedFeature(featureParam);
    }
  }, [searchParams]);

  useEffect(() => {
    if (highlightedFeature && highlightRef.current) {
      highlightRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [highlightedFeature]);

  const profile = drafts?.system_profile_draft;
  const features = drafts?.feature_drafts ?? [];
  const handleGenerateDrafts = () => {
    generateDrafts.mutateAsync()
      .then((result) => {
        const run = result.intelligence_run;
        if (run.status === "failed") {
          toast.error(run.error_details || "Draft generation failed");
          return;
        }
        toast.success(
          `Drafts generated: ${result.feature_drafts.length} feature(s)`,
        );
      })
      .catch(e => toast.error(String(e)));
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">Feature Map</h1>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => generateLinks.mutateAsync().then(() => toast.success("Code links generated")).catch(e => toast.error(String(e)))}
            disabled={generateLinks.isPending}
          >
            <Link2 className="h-4 w-4 mr-1" />
            {generateLinks.isPending ? "Mapping..." : "Generate Code Links"}
          </Button>
          <Button
            size="sm"
            onClick={handleGenerateDrafts}
            disabled={generateDrafts.isPending}
          >
            <Sparkles className="h-4 w-4 mr-1" />
            {generateDrafts.isPending ? "Generating..." : "Generate Drafts"}
          </Button>
        </div>
      </div>

      {drafts?.intelligence_run?.is_mock && (
        <div className="rounded-md border border-amber-200 bg-amber-50 dark:bg-amber-950/20 dark:border-amber-800 px-4 py-3 text-sm text-amber-800 dark:text-amber-200">
          This data is from a mock LLM provider and is for development purposes only.
        </div>
      )}
      {drafts?.intelligence_run?.status === "failed" && (
        <div className="rounded-md border border-red-200 bg-red-50 dark:bg-red-950/20 dark:border-red-800 px-4 py-3 text-sm text-red-800 dark:text-red-200">
          Draft generation failed: {drafts.intelligence_run.error_details || "Unknown error"}
        </div>
      )}

      <Tabs defaultValue="profile">
        <TabsList>
          <TabsTrigger value="profile">System Profile</TabsTrigger>
          <TabsTrigger value="features">Features ({features.length})</TabsTrigger>
          <TabsTrigger value="code-links">Code Links</TabsTrigger>
        </TabsList>

        <TabsContent value="profile">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">System Profile Draft</CardTitle>
            </CardHeader>
            <CardContent>
              {draftsLoading ? (
                <div className="space-y-3">{[1,2,3,4].map(i=><Skeleton key={i} className="h-6 w-full"/>)}</div>
              ) : !profile ? (
                <p className="text-sm text-muted-foreground text-center py-8">No profile draft yet. Generate drafts to start.</p>
              ) : (
                <dl className="space-y-4 text-sm">
                  {([
                    ["Name", profile.name],
                    ["Purpose", profile.purpose],
                    ["Target Users", profile.target_users],
                    ["Stakeholder Value", profile.stakeholder_value],
                    ["Constraints", profile.constraints],
                    ["Success Criteria", profile.success_criteria],
                  ] as const).map(([label, value]) => (
                    <div key={label}>
                      <dt className="font-medium text-muted-foreground">{label}</dt>
                      <dd className="mt-1 whitespace-pre-wrap">{value || "—"}</dd>
                    </div>
                  ))}
                  {profile.evidence?.length > 0 && (
                    <div>
                      <dt className="font-medium text-muted-foreground">Evidence</dt>
                      <dd className="mt-1 space-y-1">
                        {profile.evidence.map((e, i) => (
                          <div key={i} className="font-mono text-xs text-muted-foreground">
                            <FileText className="inline h-3 w-3 mr-1" />
                            {e.file}:{e.line_start}–{e.line_end}
                            {e.relevance && <span className="ml-2 text-foreground">{e.relevance}</span>}
                          </div>
                        ))}
                      </dd>
                    </div>
                  )}
                </dl>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="features">
          {draftsLoading ? (
            <div className="space-y-3">{[1,2,3].map(i=><Skeleton key={i} className="h-32 w-full"/>)}</div>
          ) : !features.length ? (
            <Card><CardContent className="py-8 text-center text-sm text-muted-foreground">No features discovered yet</CardContent></Card>
          ) : (
            <div className="grid gap-4 md:grid-cols-2">
              {features.map(f => {
                const acceptedLinks = featureLinks.filter(l => l.status === "accepted");
              const linkedSymbols = new Set(acceptedLinks.map(l => l.symbol_qualified_name));
              const capsFromLinks = (hierarchy?.capabilities ?? []).filter(cap =>
                cap.elements.some(el => linkedSymbols.has(el.provenance.qualified_name)),
              );
              const capsFromHierarchy = (hierarchy?.capabilities ?? []).filter(cap =>
                cap.elements.some(el => el.provenance.feature_id === f.feature_id),
              );
              const relatedCapIds = new Set<number>();
              const relatedCaps: typeof capsFromLinks = [];
              for (const cap of [...capsFromLinks, ...capsFromHierarchy]) {
                if (!relatedCapIds.has(cap.id)) {
                  relatedCapIds.add(cap.id);
                  relatedCaps.push(cap);
                }
              }
                const featureLinks = (codeLinks?.links ?? []).filter(l => l.feature_id === f.feature_id);
                const isHighlighted = highlightedFeature === f.feature_id;
                return (
                  <Card
                    key={f.id}
                    ref={isHighlighted ? highlightRef : undefined}
                    className={isHighlighted ? "ring-2 ring-primary" : ""}
                    data-testid="feature-card"
                  >
                    <CardHeader className="pb-3">
                      <div className="flex items-start justify-between">
                        <CardTitle className="text-sm">{f.name}</CardTitle>
                        <Badge variant="outline" className="text-xs">{f.feature_id}</Badge>
                      </div>
                      <CardDescription className="text-xs">{f.summary}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-2 text-xs">
                      {f.user_value && <div><span className="font-medium text-muted-foreground">Value: </span>{f.user_value}</div>}
                      {f.risks && <div><span className="font-medium text-muted-foreground">Risks: </span>{f.risks}</div>}
                      {f.evidence?.length > 0 && (
                        <div className="pt-1 space-y-0.5">
                          {f.evidence.slice(0, 3).map((e, i) => (
                            <div key={i} className="font-mono text-muted-foreground">
                              {e.file}:{e.line_start}–{e.line_end}
                            </div>
                          ))}
                          {f.evidence.length > 3 && <span className="text-muted-foreground">+{f.evidence.length - 3} more</span>}
                        </div>
                      )}
                      {relatedCaps.length > 0 && (
                        <div className="pt-1 flex flex-wrap gap-1" data-testid="feature-capability-links">
                          {relatedCaps.map(cap => (
                            <Link
                              key={cap.id}
                              to={`/capability-map?capability=${encodeURIComponent(cap.capability_key ?? cap.name)}`}
                              className="inline-flex items-center gap-1 text-primary hover:underline"
                            >
                              <Boxes className="h-3 w-3" />
                              <span>{cap.name}</span>
                            </Link>
                          ))}
                        </div>
                      )}
                      {featureLinks.length > 0 && (
                        <div className="pt-1 text-muted-foreground">
                          <span className="font-medium">{featureLinks.length} code link{featureLinks.length !== 1 ? "s" : ""}</span>
                        </div>
                      )}
                      <AddToWorkspaceButton itemType="feature" itemId={f.feature_id} label={f.name} />
                    </CardContent>
                  </Card>
                );
              })}
            </div>
          )}
        </TabsContent>

        <TabsContent value="code-links">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Feature-to-Code Links</CardTitle>
              <CardDescription>Mapping between features and code symbols</CardDescription>
            </CardHeader>
            <CardContent>
              {linksLoading ? (
                <div className="space-y-2">{[1,2,3].map(i=><Skeleton key={i} className="h-12 w-full"/>)}</div>
              ) : !codeLinks?.links?.length ? (
                <p className="text-sm text-muted-foreground text-center py-8">No code links generated yet</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b text-left">
                        <th className="pb-2 font-medium text-muted-foreground">Feature</th>
                        <th className="pb-2 font-medium text-muted-foreground">Symbol</th>
                        <th className="pb-2 font-medium text-muted-foreground">Confidence</th>
                        <th className="pb-2 font-medium text-muted-foreground">Status</th>
                        <th className="pb-2 font-medium text-muted-foreground text-right">Review</th>
                      </tr>
                    </thead>
                    <tbody>
                      {codeLinks.links.map(l => (
                        <tr key={l.id} className="border-b last:border-0">
                          <td className="py-2 text-xs">
                            <Link
                              to={`/feature-map?feature=${encodeURIComponent(l.feature_id)}`}
                              className="text-primary hover:underline"
                            >
                              {l.feature_id}
                            </Link>
                          </td>
                          <td className="py-2 font-mono text-xs">{l.symbol}</td>
                          <td className="py-2">
                            <div className="flex items-center gap-2">
                              <div className="h-1.5 w-16 rounded-full bg-muted overflow-hidden">
                                <div className="h-full rounded-full bg-primary" style={{ width: `${l.confidence * 100}%` }} />
                              </div>
                              <span className="text-xs text-muted-foreground">{(l.confidence * 100).toFixed(0)}%</span>
                            </div>
                          </td>
                          <td className="py-2">
                            <Badge variant={l.review_status === "accepted" ? "success" : l.review_status === "rejected" ? "destructive" : "secondary"}>
                              {l.review_status}
                            </Badge>
                          </td>
                          <td className="py-2 text-right">
                            <div className="flex justify-end gap-1">
                              <Button
                                variant="ghost" size="icon" className="h-7 w-7"
                                onClick={() => reviewLink.mutateAsync({ linkId: l.id, review_status: "accepted" }).catch(e => toast.error(String(e)))}
                                disabled={l.review_status === "accepted"}
                              >
                                <CheckCircle className="h-4 w-4 text-emerald-600" />
                              </Button>
                              <Button
                                variant="ghost" size="icon" className="h-7 w-7"
                                onClick={() => reviewLink.mutateAsync({ linkId: l.id, review_status: "rejected" }).catch(e => toast.error(String(e)))}
                                disabled={l.review_status === "rejected"}
                              >
                                <XCircle className="h-4 w-4 text-red-500" />
                              </Button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}

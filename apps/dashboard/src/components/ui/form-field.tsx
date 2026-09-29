import { useId, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import { Label } from "@/components/ui/label";

// Issue #466 (UX-15): 可視ラベル・補助説明・エラーを入力欄へ関連付ける。
//
// `<Label>` を入力の隣に置くだけでは、見た目は近くても支援技術には関係が
// 伝わらない (ラベルをクリックしてもフォーカスが移らず、入力のアクセシブル名も
// 空)。id / htmlFor / aria-describedby / aria-invalid の配線を呼び出し側ごとに
// 書くと必ずどこかで抜けるので、ここで 1 回だけ行う。
//
// 子は render prop で、入力に渡す属性を受け取る:
//   <FormField label="System 名" hint="..." error={error}>
//     {(field) => <Input {...field} value={name} onChange={...} />}
//   </FormField>

export interface FormFieldControlProps {
  id: string;
  "aria-describedby"?: string;
  "aria-invalid"?: true;
  "aria-required"?: true;
}

export function FormField({
  label,
  hint,
  error,
  required,
  className,
  children,
}: {
  label: ReactNode;
  hint?: ReactNode;
  /** 表示中のエラー。値があると `aria-invalid` が付き、説明として読み上げられる。 */
  error?: ReactNode;
  required?: boolean;
  className?: string;
  children: (field: FormFieldControlProps) => ReactNode;
}) {
  const baseId = useId();
  const id = `${baseId}-control`;
  const hintId = hint ? `${baseId}-hint` : undefined;
  const errorId = error ? `${baseId}-error` : undefined;
  const describedBy = [errorId, hintId].filter(Boolean).join(" ") || undefined;
  return (
    <div className={cn("space-y-2", className)}>
      <Label htmlFor={id}>
        {label}
        {required && <span className="ml-0.5 text-destructive" aria-hidden="true">*</span>}
      </Label>
      {children({
        id,
        ...(describedBy ? { "aria-describedby": describedBy } : {}),
        ...(error ? { "aria-invalid": true as const } : {}),
        ...(required ? { "aria-required": true as const } : {}),
      })}
      {hint && (
        <p id={hintId} className="text-xs text-muted-foreground">
          {hint}
        </p>
      )}
      {error && (
        <p id={errorId} className="text-xs text-destructive" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

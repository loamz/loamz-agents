"use client";

/**
 * How each A2UI component is drawn: with the console's design system, so a card follows the theme and works on a phone.
 * The schemas, data binding, list templates and actions are A2UI's own (`@a2ui/web_core`); this file is only the look.
 * Pictures, video and audio go through `media.ts`: an agent never makes the browser load an address it chose.
 */
import { createComponentImplementation, type ReactComponentImplementation } from "@a2ui/react/v0_9";
import {
  AudioPlayerApi,
  ButtonApi,
  CardApi,
  CheckBoxApi,
  ChoicePickerApi,
  ColumnApi,
  DateTimeInputApi,
  DividerApi,
  IconApi,
  ImageApi,
  ListApi,
  ModalApi,
  RowApi,
  SliderApi,
  TabsApi,
  TextApi,
  TextFieldApi,
  VideoApi,
} from "@a2ui/web_core/v0_9/basic_catalog";
import {
  Bell,
  BellOff,
  Calendar,
  CalendarDays,
  Camera,
  Check,
  CircleAlert,
  CircleHelp,
  CircleUser,
  CreditCard,
  Download,
  Ellipsis,
  EllipsisVertical,
  Eye,
  EyeOff,
  FastForward,
  Folder,
  Heart,
  HeartOff,
  House,
  Image as ImageIcon,
  Info,
  Lock,
  LockOpen,
  Mail,
  Menu,
  MapPin,
  Paperclip,
  Pause,
  Pencil,
  Phone,
  Play,
  Plus,
  Printer,
  RefreshCw,
  Rewind,
  Search,
  Send,
  Settings,
  Share2,
  ShoppingCart,
  SkipBack,
  SkipForward,
  Square,
  Star,
  StarHalf,
  Trash2,
  TriangleAlert,
  Upload,
  User,
  Volume1,
  Volume2,
  VolumeX,
  X,
  ArrowLeft,
  ArrowRight,
  type LucideIcon,
} from "lucide-react";
import { createContext, useContext, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import { Badge, Button, Checkbox, Dialog, DialogContent, DialogTrigger, Input, Tabs as DesignTabs, TabsContent, TabsList, TabsTrigger, Textarea, cn } from "@/design";
import { resolveMedia } from "./media";
import { AvatarApi, BadgeApi, CalloutApi, MetricApi, ProgressApi } from "./apis";

/** What a surface needs from the chat around it: whose workspace its files come from. */
export const A2uiEnv = createContext<{ threadId?: string | null }>({});

// The renderer resolves every dynamic value before a component sees it; the shapes are the schemas', read loosely here.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Props = Record<string, any>;
type Child = string | { id: string; basePath?: string };
type Build = (id: string, basePath?: string) => ReactNode;

const childRefs = (children: unknown): { id: string; basePath?: string }[] =>
  (Array.isArray(children) ? (children as Child[]) : []).map((c) => (typeof c === "string" ? { id: c } : c));

const ALIGN = { start: "items-start", center: "items-center", end: "items-end", stretch: "items-stretch" } as const;
const JUSTIFY = {
  start: "justify-start",
  center: "justify-center",
  end: "justify-end",
  spaceBetween: "justify-between",
  spaceAround: "justify-around",
  spaceEvenly: "justify-evenly",
  stretch: "justify-stretch",
} as const;
const TONE_TEXT = { success: "text-success", warning: "text-warning", danger: "text-danger", info: "text-accent" } as const;
const TONE_FILL = { success: "bg-success", warning: "bg-warning", danger: "bg-danger", info: "bg-accent" } as const;
const BADGE_TONE = { success: "success", warning: "warning", danger: "danger", info: "accent" } as const;

function Children({ refs, build }: { refs: { id: string; basePath?: string }[]; build: Build }) {
  return refs.map((c, i) => <span key={`${c.id}-${c.basePath ?? i}`} className="contents">{build(c.id, c.basePath)}</span>);
}

// ── text ─────────────────────────────────────────────────────────────────────
const HEADINGS = { h1: "text-xl font-bold", h2: "text-lg font-semibold", h3: "text-base font-semibold", h4: "text-sm font-semibold", h5: "text-xs font-semibold uppercase tracking-wide text-muted" } as const;

const Text = createComponentImplementation(TextApi, ({ props }: { props: Props }) => {
  const variant = (props.variant ?? "body") as keyof typeof HEADINGS | "caption" | "body";
  const text = String(props.text ?? "");
  if (variant in HEADINGS) {
    return <div role="heading" aria-level={Number(variant[1])} className={cn("text-foreground", HEADINGS[variant as keyof typeof HEADINGS])}>{text}</div>;
  }
  return (
    <div className={cn("min-w-0 text-sm leading-relaxed [&_a]:text-accent [&_a]:underline [&_code]:rounded [&_code]:bg-code [&_code]:px-1 [&_ul]:list-disc [&_ul]:pl-4", variant === "caption" ? "text-xs text-muted" : "text-foreground/90")}>
      <ReactMarkdown
        allowedElements={["p", "strong", "em", "code", "a", "ul", "ol", "li", "br"]}
        unwrapDisallowed
        components={{ a: ({ children, href }) => (/^https:\/\//.test(href ?? "") ? <a href={href} target="_blank" rel="noreferrer noopener">{children}</a> : <span>{children}</span>) }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
});

const ICONS: Record<string, LucideIcon> = {
  accountCircle: CircleUser, add: Plus, arrowBack: ArrowLeft, arrowForward: ArrowRight, attachFile: Paperclip, calendarToday: Calendar,
  call: Phone, camera: Camera, check: Check, close: X, delete: Trash2, download: Download, edit: Pencil, event: CalendarDays, error: CircleAlert,
  fastForward: FastForward, favorite: Heart, favoriteOff: HeartOff, folder: Folder, help: CircleHelp, home: House, info: Info, locationOn: MapPin,
  lock: Lock, lockOpen: LockOpen, mail: Mail, menu: Menu, moreVert: EllipsisVertical, moreHoriz: Ellipsis, notificationsOff: BellOff,
  notifications: Bell, pause: Pause, payment: CreditCard, person: User, phone: Phone, photo: ImageIcon, play: Play, print: Printer, refresh: RefreshCw,
  rewind: Rewind, search: Search, send: Send, settings: Settings, share: Share2, shoppingCart: ShoppingCart, skipNext: SkipForward,
  skipPrevious: SkipBack, star: Star, starHalf: StarHalf, starOff: Star, stop: Square, upload: Upload, visibility: Eye, visibilityOff: EyeOff,
  volumeDown: Volume1, volumeMute: VolumeX, volumeOff: VolumeX, volumeUp: Volume2, warning: TriangleAlert,
};

const Icon = createComponentImplementation(IconApi, ({ props }: { props: Props }) => {
  const name = props.name;
  if (name && typeof name === "object" && typeof name.svgPath === "string" && /^[MmLlHhVvCcSsQqTtAaZz0-9\s,.\-eE]+$/.test(name.svgPath)) {
    return <svg viewBox="0 0 24 24" className="size-5 shrink-0 text-foreground" aria-hidden><path d={name.svgPath} fill="currentColor" /></svg>;
  }
  const Glyph = ICONS[String(name)];
  return Glyph ? <Glyph className="shrink-0 text-foreground" aria-hidden /> : null;
});

// ── media ────────────────────────────────────────────────────────────────────
const IMAGE_SIZE = {
  icon: "size-6 rounded",
  avatar: "size-10 rounded-full",
  smallFeature: "size-20 rounded-lg",
  mediumFeature: "h-40 w-40 max-w-full rounded-lg",
  largeFeature: "h-64 w-full rounded-lg",
  header: "h-40 w-full rounded-lg",
} as const;
const FIT = { contain: "object-contain", cover: "object-cover", fill: "object-fill", none: "object-none", scaleDown: "object-scale-down" } as const;

function Picture({ src, alt, className, fit }: { src: string; alt: string; className: string; fit: string }) {
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");
  if (state === "failed") {
    return <div className={cn("flex items-center justify-center bg-card text-2xs text-muted", className)} title={alt}><ImageIcon aria-hidden /></div>;
  }
  return (
    <span className={cn("relative inline-block overflow-hidden bg-card", className)}>
      {/* eslint-disable-next-line @next/next/no-img-element -- the address was checked by resolveMedia; next/image would proxy it again */}
      <img
        src={src}
        alt={alt}
        loading="lazy"
        decoding="async"
        referrerPolicy="no-referrer"
        onLoad={() => setState("ready")}
        onError={() => setState("failed")}
        className={cn("size-full transition-opacity", FIT[fit as keyof typeof FIT] ?? "object-cover", state === "ready" ? "opacity-100" : "opacity-0")}
      />
      {state === "loading" && <span className="absolute inset-0 animate-pulse bg-card" aria-hidden />}
    </span>
  );
}

const Image = createComponentImplementation(ImageApi, ({ props }: { props: Props }) => {
  const { threadId } = useContext(A2uiEnv);
  const src = resolveMedia(props.url, "image", threadId);
  const size = IMAGE_SIZE[(props.variant ?? "mediumFeature") as keyof typeof IMAGE_SIZE] ?? IMAGE_SIZE.mediumFeature;
  if (!src) return <div className={cn("flex items-center justify-center bg-card text-muted", size)}><ImageIcon aria-hidden /></div>;
  return <Picture src={src} alt={String(props.description ?? "")} className={size} fit={String(props.fit ?? "cover")} />;
});

const Video = createComponentImplementation(VideoApi, ({ props }: { props: Props }) => {
  const { threadId } = useContext(A2uiEnv);
  const src = resolveMedia(props.url, "video", threadId);
  return src ? <video src={src} controls preload="metadata" className="max-h-72 w-full rounded-lg bg-black" /> : null;
});

const AudioPlayer = createComponentImplementation(AudioPlayerApi, ({ props }: { props: Props }) => {
  const { threadId } = useContext(A2uiEnv);
  const src = resolveMedia(props.url, "audio", threadId);
  return src ? (
    <figure className="space-y-1">
      {props.description && <figcaption className="text-xs text-muted">{String(props.description)}</figcaption>}
      <audio src={src} controls preload="metadata" className="w-full" />
    </figure>
  ) : null;
});

// ── layout ───────────────────────────────────────────────────────────────────
const Row = createComponentImplementation(RowApi, ({ props, buildChild }: { props: Props; buildChild: Build }) => (
  <div className={cn("flex min-w-0 flex-row flex-wrap gap-3", ALIGN[props.align as keyof typeof ALIGN] ?? "items-center", JUSTIFY[props.justify as keyof typeof JUSTIFY])}>
    <Children refs={childRefs(props.children)} build={buildChild} />
  </div>
));

const Column = createComponentImplementation(ColumnApi, ({ props, buildChild }: { props: Props; buildChild: Build }) => (
  <div className={cn("flex min-w-0 flex-col gap-2", ALIGN[props.align as keyof typeof ALIGN], JUSTIFY[props.justify as keyof typeof JUSTIFY])}>
    <Children refs={childRefs(props.children)} build={buildChild} />
  </div>
));

const List = createComponentImplementation(ListApi, ({ props, buildChild }: { props: Props; buildChild: Build }) => (
  <div className={cn("flex min-w-0 gap-2", props.direction === "horizontal" ? "flex-row overflow-x-auto pb-1" : "flex-col", ALIGN[props.align as keyof typeof ALIGN])}>
    <Children refs={childRefs(props.children)} build={buildChild} />
  </div>
));

const Card = createComponentImplementation(CardApi, ({ props, buildChild }: { props: Props; buildChild: Build }) => (
  <section className="w-full min-w-0 space-y-3 rounded-xl border border-border bg-surface p-3">{props.child ? buildChild(String(props.child)) : null}</section>
));

const Divider = createComponentImplementation(DividerApi, ({ props }: { props: Props }) =>
  props.axis === "vertical" ? <div className="w-px self-stretch bg-border" /> : <hr className="border-border" />,
);

const Tabs = createComponentImplementation(TabsApi, ({ props, buildChild }: { props: Props; buildChild: Build }) => {
  const tabs = (Array.isArray(props.tabs) ? props.tabs : []) as { title: string; child: string }[];
  if (!tabs.length) return null;
  return (
    <DesignTabs defaultValue="0" className="space-y-2">
      <TabsList className="max-w-full overflow-x-auto">
        {tabs.map((t, i) => (
          <TabsTrigger key={`${t.child}-${i}`} value={String(i)}>{String(t.title)}</TabsTrigger>
        ))}
      </TabsList>
      {tabs.map((t, i) => (
        <TabsContent key={`${t.child}-${i}`} value={String(i)}>{buildChild(t.child)}</TabsContent>
      ))}
    </DesignTabs>
  );
});

const Modal = createComponentImplementation(ModalApi, ({ props, buildChild }: { props: Props; buildChild: Build }) => (
  <Dialog>
    <DialogTrigger asChild><span className="inline-flex cursor-pointer">{buildChild(String(props.trigger))}</span></DialogTrigger>
    <DialogContent title="Details">{buildChild(String(props.content))}</DialogContent>
  </Dialog>
));

// ── actions and inputs ───────────────────────────────────────────────────────
const ActionButton = createComponentImplementation(ButtonApi, ({ props, buildChild }: { props: Props; buildChild: Build }) => (
  <Button size="lg" variant={props.variant === "primary" ? "primary" : props.variant === "borderless" ? "ghost" : "secondary"} disabled={props.isValid === false}
    onClick={() => typeof props.action === "function" && props.action()}>
    {buildChild(String(props.child))}
  </Button>
));

function Field({ label, errors, children }: { label?: string; errors?: string[]; children: ReactNode }) {
  return (
    <label className="block space-y-1 text-xs">
      {label && <span className="font-medium text-muted">{label}</span>}
      {children}
      {errors?.map((e, i) => <span key={i} className="block text-danger">{e}</span>)}
    </label>
  );
}

const TextField = createComponentImplementation(TextFieldApi, ({ props }: { props: Props }) => {
  const common = { value: String(props.value ?? ""), onChange: (e: { target: { value: string } }) => props.setValue?.(e.target.value) };
  return (
    <Field label={props.label} errors={props.validationErrors}>
      {props.variant === "longText" ? <Textarea rows={4} {...common} /> : <Input type={props.variant === "number" ? "number" : props.variant === "obscured" ? "password" : "text"} size="lg" {...common} />}
    </Field>
  );
});

const CheckBox = createComponentImplementation(CheckBoxApi, ({ props }: { props: Props }) => (
  <Checkbox label={String(props.label ?? "")} checked={Boolean(props.value)} onChange={(e) => props.setValue?.(e.target.checked)} />
));

const ChoicePicker = createComponentImplementation(ChoicePickerApi, ({ props }: { props: Props }) => {
  const options = (Array.isArray(props.options) ? props.options : []) as { label: string; value: string }[];
  const chosen: string[] = Array.isArray(props.value) ? props.value : props.value ? [String(props.value)] : [];
  const single = props.variant === "mutuallyExclusive";
  const toggle = (value: string) => props.setValue?.(single ? [value] : chosen.includes(value) ? chosen.filter((v) => v !== value) : [...chosen, value]);
  return (
    <Field label={props.label}>
      <span className="flex flex-wrap gap-1.5">
        {options.map((o) => (
          <Button key={o.value} size="sm" variant={chosen.includes(o.value) ? "accent" : "secondary"} aria-pressed={chosen.includes(o.value)} onClick={() => toggle(o.value)} className="rounded-full">
            {String(o.label)}
          </Button>
        ))}
      </span>
    </Field>
  );
});

const Slider = createComponentImplementation(SliderApi, ({ props }: { props: Props }) => (
  <Field label={`${props.label ?? ""} ${props.value ?? ""}`.trim()}>
    <Input type="range" min={props.min ?? 0} max={props.max ?? 100} value={Number(props.value ?? 0)} onChange={(e) => props.setValue?.(Number(e.target.value))} className="h-auto border-0 bg-transparent p-0 accent-accent" />
  </Field>
));

const DateTimeInput = createComponentImplementation(DateTimeInputApi, ({ props }: { props: Props }) => {
  const type = props.enableDate !== false && props.enableTime ? "datetime-local" : props.enableTime ? "time" : "date";
  return (
    <Field label={props.label}>
      <Input type={type} size="lg" value={String(props.value ?? "")} min={props.min} max={props.max} onChange={(e) => props.setValue?.(e.target.value)} />
    </Field>
  );
});

// ── added to the basic catalog ───────────────────────────────────────────────
const Metric = createComponentImplementation(MetricApi, ({ props }: { props: Props }) => (
  <div className="min-w-32 flex-1 rounded-lg border border-border bg-card p-2.5">
    <div className="truncate text-2xs text-muted">{String(props.label ?? "")}</div>
    <div className={cn("truncate text-xl font-semibold tabular-nums", props.trend === "up" ? "text-success" : props.trend === "down" ? "text-danger" : "text-foreground")}>{String(props.value ?? "")}</div>
    {props.detail && <div className="truncate text-2xs text-muted">{String(props.detail)}</div>}
  </div>
));

const BadgeImpl = createComponentImplementation(BadgeApi, ({ props }: { props: Props }) => (
  <Badge tone={props.tone ? BADGE_TONE[props.tone as keyof typeof BADGE_TONE] : "neutral"}>{String(props.text ?? "")}</Badge>
));

const Progress = createComponentImplementation(ProgressApi, ({ props }: { props: Props }) => {
  const max = typeof props.max === "number" && props.max > 0 ? props.max : 100;
  const pct = Math.min(100, Math.max(0, (Number(props.value ?? 0) / max) * 100));
  const tone = (props.tone ?? "info") as keyof typeof TONE_FILL;
  return (
    <div className="w-full space-y-1">
      {props.label && (
        <div className="flex justify-between text-xs"><span className="text-foreground">{String(props.label)}</span><span className={cn("tabular-nums", TONE_TEXT[tone])}>{Math.round(pct)}%</span></div>
      )}
      <div role="progressbar" aria-label={String(props.label ?? "progress")} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)} className="h-1.5 overflow-hidden rounded-full bg-border">
        <div className={cn("h-full rounded-full", TONE_FILL[tone])} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
});

const WASH = {
  success: "border-success/40 bg-success/10",
  warning: "border-warning/40 bg-warning/10",
  danger: "border-danger/40 bg-danger/10",
  info: "border-accent/40 bg-accent/10",
} as const;

const Callout = createComponentImplementation(CalloutApi, ({ props }: { props: Props }) => {
  const tone = (props.tone ?? "info") as keyof typeof WASH;
  return (
    <div role="note" className={cn("w-full rounded-lg border p-3", WASH[tone])}>
      {props.title && <div className={cn("text-sm font-semibold", TONE_TEXT[tone])}>{String(props.title)}</div>}
      <p className="text-xs leading-relaxed text-foreground/90">{String(props.text ?? "")}</p>
    </div>
  );
});

const initials = (name: string) => name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase()).join("");

const Avatar = createComponentImplementation(AvatarApi, ({ props }: { props: Props }) => (
  <span title={String(props.name ?? "")} className={cn("inline-flex shrink-0 items-center justify-center rounded-md bg-badge font-bold text-badge-foreground", props.size === "lg" ? "size-10 text-xs" : props.size === "sm" ? "size-6 text-2xs" : "size-8 text-2xs")}>
    {initials(String(props.name ?? ""))}
  </span>
));

export const implementations: ReactComponentImplementation[] = [
  Text, Image, Icon, Video, AudioPlayer, Row, Column, List, Card, Tabs, Modal, Divider, ActionButton, TextField, CheckBox, ChoicePicker, Slider, DateTimeInput,
  Metric, BadgeImpl, Progress, Callout, Avatar,
];

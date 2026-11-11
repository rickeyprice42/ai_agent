import ReactMarkdown, { type Options } from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math-extended";
import "katex/dist/katex.min.css";

export default function MathMarkdown(props: Options) {
  return <ReactMarkdown
    {...props}
    remarkPlugins={[remarkGfm, [remarkMath, { singleDollarTextMath: false }]]}
    rehypePlugins={[[rehypeKatex, { trust: false, maxExpand: 500, maxSize: 10 }]]}
  />;
}

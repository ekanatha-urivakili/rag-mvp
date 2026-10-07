import ReactMarkdown from "react-markdown";

/**
 * Renders model output. Model text is untrusted (it can echo prompt-injected document content), so:
 * raw HTML is dropped (skipHtml), images are not rendered (no exfiltration via image URLs), and
 * react-markdown's default urlTransform strips javascript:/data: links.
 */
export function Markdown({ children }: { children: string }) {
  return (
    <div className="prose-chat text-[0.95rem] leading-relaxed break-words">
      <ReactMarkdown
        skipHtml
        disallowedElements={["img"]}
        unwrapDisallowed
        components={{
          a: ({ href, children: text }) => (
            <a href={href} target="_blank" rel="noopener noreferrer nofollow">
              {text}
            </a>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}

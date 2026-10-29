import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RichText, joinSegments } from "./richText";

describe("RichText", () => {
  it("shows HTML in a model's answer as text, never as markup", () => {
    const { container } = render(<RichText text={'Fee: <img src=x onerror="alert(1)"> **KES 35**'} />);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText(/<img src=x/)).toBeInTheDocument();
    expect(screen.getByText("KES 35").tagName).toBe("STRONG");
  });

  it("turns lines starting with a dash into a list", () => {
    const { container } = render(<RichText text={"Your balances:\n\n- Everyday: **KES 41,230**\n- Goal Saver: KES 128,500"} />);
    expect(container.querySelectorAll("li")).toHaveLength(2);
  });

  it("places citation marks where each cited segment ends", () => {
    const text = joinSegments([
      { text: "It costs KES 35 ", cites: [1] },
      { text: "and arrives at once.", cites: [] },
    ]);
    render(<RichText text={text} renderCite={(n, key) => <sup key={key}>[{n.join(",")}]</sup>} />);
    const mark = screen.getByText("[1]");
    expect(mark.previousSibling?.textContent).toBe("It costs KES 35");
  });

  it("keeps a space between sentences that arrive in separate segments", () => {
    const text = joinSegments([
      { text: "They open at 9:00 am that day.", cites: [1] },
      { text: "Branches are closed on Sundays.", cites: [] },
    ]);
    expect(text).toContain(" Branches");
    // Segments that already carry their spacing, or continue a sentence, are joined as they are.
    expect(joinSegments([{ text: "It costs KES 35 ", cites: [] }, { text: "a month.", cites: [] }])).toBe("It costs KES 35 a month.");
    expect(joinSegments([{ text: "The fee is KES 30", cites: [] }, { text: ", taken monthly.", cites: [] }])).toBe("The fee is KES 30, taken monthly.");
  });
});

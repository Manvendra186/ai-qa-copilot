import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import Counter from "../components/Counter";

describe("Counter", () => {
  it("renders the initial count", () => {
    render(<Counter />);
    expect(screen.getByRole("button")).toHaveTextContent("Count: 0");
  });
});

/**
 * Vitest setup — runs once before all tests.
 *
 * Pulls in `@testing-library/jest-dom`'s custom matchers
 * (`toBeInTheDocument`, `toHaveTextContent`, etc.) so component tests
 * can assert against the rendered DOM the same way the React Testing
 * Library docs show.
 */
import '@testing-library/jest-dom/vitest'

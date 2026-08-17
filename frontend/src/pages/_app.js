import Head from 'next/head';
import '../styles/globals.css';

export default function App({ Component, pageProps }) {
  return (
    <>
      <Head>
        {/* Site-wide defaults. Pages that set their own <title> or
            description in their local <Head> override these. */}
        <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <meta
          name="description"
          content="CloudOptimizer maps what your cloud services actually depend on, finds the outage hiding in the architecture, and tells you the cheapest way to remove it."
        />
      </Head>
      <Component {...pageProps} />
    </>
  );
}

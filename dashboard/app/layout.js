import { Montserrat } from 'next/font/google';
import './globals.css';

const montserrat = Montserrat({
  subsets: ['latin'],
  weight: ['700'],
  variable: '--font-montserrat',
});

export const metadata = {
  title: 'FinBoard — Market Signals',
  description: 'Daily quantitative signal dashboard for NSE 500 stocks',
};

export default function RootLayout({ children }) {
  return (
    <html lang="en" className={montserrat.variable}>
      <body>{children}</body>
    </html>
  );
}

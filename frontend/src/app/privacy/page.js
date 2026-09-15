import React from 'react';

export const metadata = {
  title: "Privacy Policy - Mom's Pride Accounting",
  description: "Privacy Policy for Mom's Pride Accounting application",
};

export default function PrivacyPolicy() {
  const lastUpdated = "September 16, 2026";

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900 pb-24 text-gray-900 dark:text-gray-100">
      <div className="max-w-3xl mx-auto px-4 py-8 sm:px-6 lg:px-8">
        <header className="mb-10 text-center">
          <h1 className="text-3xl font-extrabold tracking-tight sm:text-4xl text-gray-900 dark:text-white mb-2">
            Privacy Policy
          </h1>
          <p className="text-lg text-gray-600 dark:text-gray-400">
            Mom&apos;s Pride Accounting
          </p>
          <p className="text-sm text-gray-500 dark:text-gray-500 mt-2">
            Last Updated: {lastUpdated}
          </p>
        </header>

        <main className="prose prose-blue dark:prose-invert max-w-none">
          <p>
            This Privacy Policy explains how Mom&apos;s Pride Accounting (&quot;we&quot;, &quot;us&quot;, or &quot;our&quot;) collects, 
            uses, and protects information when you use our internal business accounting application 
            and associated WhatsApp integration.
          </p>

          <h2 className="text-xl font-bold mt-8 mb-4">1. Information We Receive</h2>
          <p className="mb-4">
            When you interact with our application, particularly through our WhatsApp integration, we may receive and process the following types of information:
          </p>
          <ul className="list-disc pl-6 mb-6 space-y-2">
            <li><strong>WhatsApp Sender Information:</strong> Information such as your WhatsApp phone number and profile name, when provided by Meta.</li>
            <li><strong>Invoice Documents:</strong> Images or PDF documents sent to the application&apos;s connected WhatsApp number.</li>
            <li><strong>Extracted Accounting Information:</strong> Information contained within those invoices, such as supplier names, invoice numbers, dates, item descriptions, quantities, prices, taxes, and total amounts.</li>
            <li><strong>Technical Information:</strong> Standard technical data required to securely operate, maintain, and troubleshoot the service.</li>
          </ul>

          <h2 className="text-xl font-bold mt-8 mb-4">2. How Information is Used</h2>
          <p className="mb-4">
            The information we collect is strictly used for accounting and operational purposes, specifically to:
          </p>
          <ul className="list-disc pl-6 mb-6 space-y-2">
            <li>Receive and process invoice documents.</li>
            <li>Extract relevant accounting information using automated processing.</li>
            <li>Create purchase drafts for internal review workflows.</li>
            <li>Allow authorized users to review, edit, and approve the extracted information.</li>
            <li>Post approved accounting entries directly to the connected Tally accounting system.</li>
            <li>Maintain application security, ensure reliability, perform message deduplication, and support auditability.</li>
          </ul>

          <h2 className="text-xl font-bold mt-8 mb-4">3. WhatsApp and Meta Integration</h2>
          <p className="mb-6">
            Our application receives documents via WhatsApp. Please note that WhatsApp messages and media are 
            transmitted through Meta&apos;s WhatsApp Business Platform. Meta processes this WhatsApp data according 
            to Meta&apos;s applicable terms and privacy policies. We encourage you to review Meta&apos;s policies to 
            understand how they handle data transmitted across their network.
          </p>

          <h2 className="text-xl font-bold mt-8 mb-4">4. Data Sharing</h2>
          <p className="mb-6">
            We do not sell your invoice or accounting data to any third parties. However, your data may be 
            processed by trusted service providers required to operate the application. This includes Meta&apos;s 
            WhatsApp Business Platform (for message transmission) and third-party AI/OCR processing services 
            used exclusively to extract text and structured data from your invoice documents.
          </p>

          <h2 className="text-xl font-bold mt-8 mb-4">5. Data Retention</h2>
          <p className="mb-6">
            We retain invoice files and the extracted accounting information as necessary to fulfill our 
            accounting operations, facilitate the review process, perform system troubleshooting, and satisfy 
            audit or legal recordkeeping requirements.
          </p>

          <h2 className="text-xl font-bold mt-8 mb-4">6. Security</h2>
          <p className="mb-6">
            We implement reasonable technical and organizational measures designed to protect the data we 
            process against unauthorized access, loss, or alteration. While we strive to protect your 
            information, no system or transmission can be guaranteed to be entirely secure.
          </p>

          <h2 className="text-xl font-bold mt-8 mb-4">7. User Rights</h2>
          <p className="mb-6">
            Authorized users may request the deletion or correction of data processed by the application. 
            Please note that these requests are subject to internal accounting policies and mandatory legal 
            recordkeeping requirements that may prevent immediate deletion of certain financial records.
          </p>

          <h2 className="text-xl font-bold mt-8 mb-4">8. Contact Us</h2>
          <p className="mb-6">
            If you have any questions or requests regarding this Privacy Policy or how we handle your data, 
            please contact us at: <a href="mailto:shubhbhisinghal@gmail.com" className="text-blue-600 dark:text-blue-400 hover:underline">shubhbhisinghal@gmail.com</a>
          </p>
        </main>
      </div>
    </div>
  );
}

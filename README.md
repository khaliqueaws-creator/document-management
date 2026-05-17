# Intelligent Document Management Platform

A Django-based intelligent document management application deployed on OpenShift CRC using containerized services and persistent storage.

## Overview

This project was built as a learning and enterprise-style architecture platform to explore:

* Document management
* OCR and intelligent document processing
* OpenShift container deployment
* Authentication and authorization
* Metadata-driven search
* AI-powered document enhancements

The application allows users to upload documents, assign metadata, search content, and preview supported files directly in the browser.

---

## Features

* Document upload and storage
* Metadata tagging and search
* OCR text extraction
* Okta OIDC authentication
* Role-based access control
* Persistent storage using OpenShift PVC
* Containerized deployment with Docker
* OpenShift CRC deployment support
* Cloudflare Tunnel public demo support

---

## Technology Stack

| Layer              | Technology        |
| ------------------ | ----------------- |
| Frontend           | Django Templates  |
| Backend            | Python / Django   |
| Database           | MySQL             |
| OCR                | Tesseract OCR     |
| Authentication     | Okta OIDC         |
| Container Platform | OpenShift CRC     |
| Containerization   | Docker            |
| Storage            | OpenShift PVC     |
| Cloud Exposure     | Cloudflare Tunnel |

---

## Architecture

Browser → OpenShift Route → Django Application → MySQL Database + Persistent File Storage

---

## Current Capabilities

* Upload PDFs, images, and documents
* Store searchable metadata
* Extract OCR text from uploaded files
* Search using metadata and extracted text
* Secure login using Okta
* Deploy locally on OpenShift CRC

---

## Planned Enhancements

* AI-based document classification
* Automatic metadata extraction
* Semantic/vector search
* RAG-based document assistant
* Workflow and approval engine
* Document versioning
* Intelligent capture interface
* Async OCR processing using Celery
* PostgreSQL + pgvector integration

---

## Learning Goals

This project is intended to simulate enterprise content management and intelligent document processing systems similar to modern ECM/IDP platforms while using open-source technologies.

---

## Author

Khalique AWS

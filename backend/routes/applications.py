from flask import Blueprint, request, jsonify
from bson.objectid import ObjectId
from utils.db import db
from datetime import datetime

# Non-fatal notification imports
try:
    from utils.email import (
        send_application_received,
        send_application_accepted,
        send_application_rejected,
        send_interview_scheduled
    )
    from utils.calendar import generate_interview_ics_now
    NOTIFICATIONS_AVAILABLE = True
except Exception as _notif_err:
    print(f"[WARN] Notifications not available: {_notif_err}")
    NOTIFICATIONS_AVAILABLE = False

applications_bp = Blueprint('applications', __name__)

@applications_bp.route('/', methods=['POST'])
def submit_application():
    """Submit a job application"""
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return jsonify({"error": "Missing or invalid authorization token"}), 401
    
    token = auth_header.split(' ')[1]
    if not ObjectId.is_valid(token):
        return jsonify({"error": "Invalid token"}), 401
    
    data = request.json
    job_id = data.get('jobId')
    
    if not job_id or not ObjectId.is_valid(job_id):
        return jsonify({"error": "Invalid job ID"}), 400
    
    # Check if user exists
    user = db.users.find_one({"_id": ObjectId(token)})
    if not user or user['role'] != 'candidate':
        return jsonify({"error": "Only candidates can submit applications"}), 403
    
    # Validate required profile fields
    required_fields = ['firstName', 'lastName', 'email', 'phone']
    missing_fields = [field for field in required_fields if not user.get(field)]
    
    if missing_fields:
        return jsonify({
            "error": f"Please complete your profile. Missing: {', '.join(missing_fields)}"
        }), 400
    
    # Check if user has uploaded resume
    if not user.get('resume') or not user.get('resume', {}).get('rawText'):
        return jsonify({
            "error": "Please upload your resume before applying"
        }), 400
    
    # Check if user has skills
    if not user.get('skills') or len(user.get('skills', [])) == 0:
        return jsonify({
            "error": "Please add your skills to your profile before applying"
        }), 400
    
    # Check if job exists and is open
    job = db.jobs.find_one({"_id": ObjectId(job_id)})
    if not job:
        return jsonify({"error": "Job not found"}), 404
    
    if job.get('status') in ['closed', 'filled']:
        return jsonify({"error": "This job is no longer accepting applications"}), 400
    
    # Check if max applications limit reached
    if job.get('maxApplicationsEnabled') and job.get('maxApplications'):
        current_count = db.applications.count_documents({"jobId": ObjectId(job_id)})
        if current_count >= job.get('maxApplications'):
            # Auto-close the job
            db.jobs.update_one(
                {"_id": ObjectId(job_id)},
                {"$set": {"status": "closed", "closedAt": datetime.now()}}
            )
            return jsonify({"error": "This job has reached its maximum number of applications"}), 400
    
    # Check if already applied
    existing_application = db.applications.find_one({
        "jobId": ObjectId(job_id),
        "candidateId": ObjectId(token)
    })
    
    if existing_application:
        return jsonify({"error": "You have already applied for this job"}), 400
    
    # Score resume against job requirements using AI
    resume_score = 0
    resume_evaluation = None
    is_eligible = False
    rejection_reason = None
    
    try:
        from models.metis.evaluator import evaluate_candidate
        
        resume_text = user.get('resume', {}).get('rawText', '')
        github_url = user.get('githubUrl')
        portfolio_url = user.get('portfolioUrl')
        
        # Run METIS AI evaluation
        resume_evaluation = evaluate_candidate(
            resume_text=resume_text,
            github_url=github_url,
            portfolio_url=portfolio_url,
            jd_text=job.get('description', '')
        )
        
        resume_score = resume_evaluation.get('overall_score', 0)
        
        # Check eligibility: score must be >= 20/100
        if resume_score < 20:
            rejection_reason = f"Resume score ({resume_score}/100) is below the minimum threshold of 20. Your qualifications do not meet the job requirements."
            is_eligible = False
        else:
            # Additional check: verify relevance through risk signals
            risk_signals = resume_evaluation.get('risk_signals', [])
            critical_risks = [r for r in risk_signals if 'irrelevant' in r.lower() or 'mismatch' in r.lower()]
            
            if len(critical_risks) > 2:
                rejection_reason = "Your profile does not align with the job requirements. Please review the job description and apply for positions that better match your experience."
                is_eligible = False
            else:
                is_eligible = True
    
    except Exception as e:
        print(f"Resume scoring error: {str(e)}")
        # If scoring fails, allow application but with 0 score
        is_eligible = True
        resume_score = 0
    
    # Reject if not eligible - DO NOT create application record
    # This allows candidates to improve their resume and try again
    if not is_eligible:
        return jsonify({
            "error": rejection_reason,
            "resumeScore": resume_score,
            "eligible": False,
            "message": "Your application was not submitted. Please improve your qualifications and try again."
        }), 400
    
    # Create application for eligible candidate
    application = {
        "jobId": ObjectId(job_id),
        "candidateId": ObjectId(token),
        "candidateName": f"{user.get('firstName', '')} {user.get('lastName', '')}".strip(),
        "candidateEmail": user.get('email'),
        "status": "pending",  # pending, under_review, assessment_sent, assessment_completed, rejected, accepted
        "stage": "resume_reviewed",  # application_submitted, resume_reviewed, assessment_pending, assessment_completed, interview_scheduled, offer_sent
        "appliedAt": datetime.now(),
        "resumeScore": resume_score,  # Round 1 score (30% of final)
        "metisEvaluation": resume_evaluation,
        "evaluatedAt": datetime.now(),
        "eligible": True,
        "profileSnapshot": {
            "firstName": user.get('firstName', ''),
            "lastName": user.get('lastName', ''),
            "email": user.get('email', ''),
            "skills": user.get('skills', []),
            "experience": user.get('experience', {}),
            "education": user.get('education', []),
            "projects": user.get('projects', []),
            "certifications": user.get('certifications', []),
            "phone": user.get('phone', ''),
            "linkedinUrl": user.get('linkedinUrl', ''),
            "githubUrl": user.get('githubUrl', ''),
            "portfolioUrl": user.get('portfolioUrl', ''),
            "resumeText": user.get('resume', {}).get('rawText', '') if isinstance(user.get('resume'), dict) else ''
        },
        "notes": [],
        "timeline": [{
            "event": "Application Submitted",
            "timestamp": datetime.now(),
            "description": f"Candidate submitted application (Resume Score: {resume_score}/100)"
        }]
    }
    
    result = db.applications.insert_one(application)
    
    # Increment application count
    db.jobs.update_one(
        {"_id": ObjectId(job_id)},
        {"$inc": {"applicationCount": 1}}
    )
    
    # Check if max applications reached after this application
    if job.get('maxApplicationsEnabled') and job.get('maxApplications'):
        updated_job = db.jobs.find_one({"_id": ObjectId(job_id)})
        if updated_job.get('applicationCount', 0) >= job.get('maxApplications'):
            db.jobs.update_one(
                {"_id": ObjectId(job_id)},
                {"$set": {"status": "closed", "closedAt": datetime.now()}}
            )
    
    # Auto-update all pending applications to under_review
    db.applications.update_many(
        {"jobId": ObjectId(job_id), "status": "pending"},
        {"$set": {"status": "under_review"}}
    )
    
    # Create assessment for this application
    assessment = {
        "jobId": ObjectId(job_id),
        "candidateId": ObjectId(token),
        "applicationId": result.inserted_id,
        "status": "pending",
        "questions": [],
        "responses": [],
        "createdAt": datetime.now()
    }
    
    assessment_result = db.assessments.insert_one(assessment)

    # ── Email notification: application received ──
    if NOTIFICATIONS_AVAILABLE:
        try:
            send_application_received(
                candidate_email=user.get('email', ''),
                candidate_name=f"{user.get('firstName', '')} {user.get('lastName', '')}".strip(),
                job_title=job.get('title', 'the position'),
                resume_score=resume_score
            )
        except Exception as _e:
            print(f"[WARN] Application received email failed: {_e}")

    return jsonify({
        "message": "Application submitted successfully",
        "applicationId": str(result.inserted_id),
        "assessmentId": str(assessment_result.inserted_id),
        "status": "under_review",
        "resumeScore": resume_score,
        "eligible": True,
        "note": "Your resume has been evaluated. You are eligible for the interview round."
    }), 201

@applications_bp.route('/job/<job_id>', methods=['GET'])
def get_job_applications(job_id):
    """Get all applications for a job"""
    if not ObjectId.is_valid(job_id):
        return jsonify({"error": "Invalid job ID"}), 400
    
    applications = list(db.applications.find({"jobId": ObjectId(job_id)}))
    
    for app in applications:
        app['_id'] = str(app['_id'])
        app['jobId'] = str(app['jobId'])
        app['candidateId'] = str(app['candidateId'])
        app['appliedAt'] = app['appliedAt'].isoformat() if app.get('appliedAt') else None
        
        # Get assessment score if available
        assessment = db.assessments.find_one({
            "applicationId": app['_id'] if isinstance(app['_id'], ObjectId) else ObjectId(app['_id'])
        })
        
        if assessment and assessment.get('status') == 'completed':
            app['assessmentScore'] = assessment.get('overallScore', assessment.get('score', 0))
        else:
            app['assessmentScore'] = None

        # -- Attach Bias Audit Trail and Configurable Weights --
        job = db.jobs.find_one({"_id": ObjectId(job_id)})
        r1_w = (job.get('round1Weight', 30) / 100.0) if job else 0.30
        r2_w = (job.get('round2Weight', 70) / 100.0) if job else 0.70

        resume_score = app.get('resumeScore', app.get('round1Score', 0))
        interview_score = app.get('interviewScore', app.get('round2Score', 0))
        metis_eval = app.get('metisEvaluation', {})
        section_scores = metis_eval.get('section_scores', {}) if metis_eval else {}

        app['scoringBreakdown'] = {
            'round1Weight': int(r1_w * 100),
            'round2Weight': int(r2_w * 100),
            'round1Contribution': round(resume_score * r1_w, 1),
            'round2Contribution': round(interview_score * r2_w, 1),
            'criteriaBreakdown': [
                {
                    'name': 'Technical Skills',
                    'score': section_scores.get('skill_evidence', 0),
                    'weight': 30,
                    'contribution': round(section_scores.get('skill_evidence', 0) / 30 * 100, 1) if section_scores.get('skill_evidence') else 0
                },
                {
                    'name': 'Project Quality',
                    'score': section_scores.get('project_authenticity', 0),
                    'weight': 25,
                    'contribution': round(section_scores.get('project_authenticity', 0) / 25 * 100, 1) if section_scores.get('project_authenticity') else 0
                },
                {
                    'name': 'Professional Experience',
                    'score': section_scores.get('professional_signals', 0),
                    'weight': 15,
                    'contribution': round(section_scores.get('professional_signals', 0) / 15 * 100, 1) if section_scores.get('professional_signals') else 0
                },
                {
                    'name': 'Impact & Outcomes',
                    'score': section_scores.get('impact_outcomes', 0),
                    'weight': 15,
                    'contribution': round(section_scores.get('impact_outcomes', 0) / 15 * 100, 1) if section_scores.get('impact_outcomes') else 0
                },
                {
                    'name': 'Resume Integrity',
                    'score': section_scores.get('resume_integrity', 0),
                    'weight': 15,
                    'contribution': round(section_scores.get('resume_integrity', 0) / 15 * 100, 1) if section_scores.get('resume_integrity') else 0
                }
            ]
        }

        # -- Attach Fraud Detection Flags (Anomaly) --
        # Fetch flags from the latest interview session for this application
        latest_session = db.interview_sessions.find_one(
            {"applicationId": ObjectId(app['_id'])},
            sort=[("updatedAt", -1)]
        )
        if latest_session and latest_session.get("anomaly_flags"):
            app["anomalyFlags"] = latest_session["anomaly_flags"]
        else:
            app["anomalyFlags"] = app.get("anomalyFlags", [])
    
    # Sort by assessment score (highest first), then by applied date
    applications.sort(key=lambda x: (x['assessmentScore'] if x['assessmentScore'] is not None else -1, x.get('appliedAt', '')), reverse=True)
    
    return jsonify({"applications": applications})

@applications_bp.route('/candidate/<candidate_id>', methods=['GET'])
def get_candidate_applications(candidate_id):
    """Get all applications by a candidate"""
    if not ObjectId.is_valid(candidate_id):
        return jsonify({"error": "Invalid candidate ID"}), 400
    
    applications = list(db.applications.find({"candidateId": ObjectId(candidate_id)}))
    
    for app in applications:
        app['_id'] = str(app['_id'])
        app['jobId'] = str(app['jobId'])
        app['candidateId'] = str(app['candidateId'])
        app['appliedAt'] = app['appliedAt'].isoformat() if app.get('appliedAt') else None
        
        # Get job details
        job = db.jobs.find_one({"_id": ObjectId(app['jobId'])})
        if job:
            app['jobTitle'] = job.get('title', 'Unknown Job')
            app['jobCompany'] = job.get('company', '')
    
    return jsonify({"applications": applications})

@applications_bp.route('/<application_id>', methods=['GET'])
def get_application(application_id):
    """Get a specific application with scoring details"""
    if not ObjectId.is_valid(application_id):
        return jsonify({"error": "Invalid application ID"}), 400
    
    application = db.applications.find_one({"_id": ObjectId(application_id)})
    
    if not application:
        return jsonify({"error": "Application not found"}), 404
    
    application['_id'] = str(application['_id'])
    application['jobId'] = str(application['jobId'])
    application['candidateId'] = str(application['candidateId'])
    application['appliedAt'] = application['appliedAt'].isoformat() if application.get('appliedAt') else None
    
    # --- Bias Audit Trail: per-criterion score breakdown ---
    # Fetch dynamic weights from the parent job
    job = db.jobs.find_one({"_id": ObjectId(application['jobId'])})
    r1_w = (job.get('round1Weight', 30) / 100.0) if job else 0.30
    r2_w = (job.get('round2Weight', 70) / 100.0) if job else 0.70

    resume_score = application.get('resumeScore', application.get('round1Score', 0))
    interview_score = application.get('interviewScore', application.get('round2Score', 0))

    # Always surface the breakdown, even if finalScore is not yet set
    metis_eval = application.get('metisEvaluation', {})
    section_scores = metis_eval.get('section_scores', {}) if metis_eval else {}

    application['scoringBreakdown'] = {
        'round1': {
            'name': 'Resume Evaluation',
            'score': resume_score,
            'weight': f'{int(r1_w * 100)}%',
            'contribution': round(resume_score * r1_w, 1)
        },
        'round2': {
            'name': 'Interview',
            'score': interview_score,
            'weight': f'{int(r2_w * 100)}%',
            'contribution': round(interview_score * r2_w, 1)
        },
        'finalScore': application.get('finalScore', round(resume_score * r1_w + interview_score * r2_w, 1)),
        # Per-criterion breakdown from METIS evaluator for HR explainability
        'criteriaBreakdown': [
            {
                'criterion': 'Technical Skills',
                'score': section_scores.get('skill_evidence', 0),
                'maxScore': 30,
                'percentage': round(section_scores.get('skill_evidence', 0) / 30 * 100, 1) if section_scores.get('skill_evidence') else 0
            },
            {
                'criterion': 'Project Quality',
                'score': section_scores.get('project_authenticity', 0),
                'maxScore': 25,
                'percentage': round(section_scores.get('project_authenticity', 0) / 25 * 100, 1) if section_scores.get('project_authenticity') else 0
            },
            {
                'criterion': 'Professional Experience',
                'score': section_scores.get('professional_signals', 0),
                'maxScore': 15,
                'percentage': round(section_scores.get('professional_signals', 0) / 15 * 100, 1) if section_scores.get('professional_signals') else 0
            },
            {
                'criterion': 'Impact & Outcomes',
                'score': section_scores.get('impact_outcomes', 0),
                'maxScore': 15,
                'percentage': round(section_scores.get('impact_outcomes', 0) / 15 * 100, 1) if section_scores.get('impact_outcomes') else 0
            },
            {
                'criterion': 'Resume Integrity',
                'score': section_scores.get('resume_integrity', 0),
                'maxScore': 15,
                'percentage': round(section_scores.get('resume_integrity', 0) / 15 * 100, 1) if section_scores.get('resume_integrity') else 0
            }
        ],
        'confidenceLevel': metis_eval.get('confidence_level', 'unknown') if metis_eval else 'unknown',
        'strengthSignals': metis_eval.get('strength_signals', []) if metis_eval else [],
        'riskSignals': metis_eval.get('risk_signals', []) if metis_eval else []
    }
    
    return jsonify(application)

@applications_bp.route('/<application_id>', methods=['PUT'])
def update_application(application_id):
    """Update application status/stage"""
    if not ObjectId.is_valid(application_id):
        return jsonify({"error": "Invalid application ID"}), 400
    
    data = request.json
    allowed_updates = ['status', 'stage', 'notes']
    
    update_data = {k: v for k, v in data.items() if k in allowed_updates}
    
    if not update_data:
        return jsonify({"error": "No valid fields to update"}), 400
    
    # Add timeline event
    if 'status' in update_data or 'stage' in update_data:
        timeline_event = {
            "event": f"Status changed to {update_data.get('status', '')} - {update_data.get('stage', '')}",
            "timestamp": datetime.now(),
            "description": data.get('note', '')
        }
        
        db.applications.update_one(
            {"_id": ObjectId(application_id)},
            {"$push": {"timeline": timeline_event}}
        )

        # ── Email notification: interview scheduled ──
        if update_data.get('stage') == 'interview_scheduled' and NOTIFICATIONS_AVAILABLE:
            try:
                application = db.applications.find_one({"_id": ObjectId(application_id)})
                if application:
                    cand_email = application.get('candidateEmail', '')
                    cand_name = application.get('candidateName', 'Candidate')
                    j = db.jobs.find_one({'_id': application.get('jobId')})
                    job_title = j.get('title', 'the position') if j else 'the position'
                    
                    # Generate an ICS file for 24 hours from now
                    ics_bytes = generate_interview_ics_now(cand_name, job_title, hours_from_now=24)
                    
                    send_interview_scheduled(cand_email, cand_name, job_title, ics_bytes=ics_bytes)
            except Exception as _e:
                print(f"[WARN] Interview scheduled email failed: {_e}")
    
    result = db.applications.update_one(
        {"_id": ObjectId(application_id)},
        {"$set": update_data}
    )
    
    if result.matched_count == 0:
        return jsonify({"error": "Application not found"}), 404
    
    return jsonify({"message": "Application updated successfully"})

@applications_bp.route('/<application_id>/select', methods=['POST'])
def select_candidate(application_id):
    """Select a candidate and close the job"""
    if not ObjectId.is_valid(application_id):
        return jsonify({"error": "Invalid application ID"}), 400
    
    application = db.applications.find_one({"_id": ObjectId(application_id)})
    if not application:
        return jsonify({"error": "Application not found"}), 404
    
    job_id = application['jobId']
    candidate_id = application['candidateId']
    
    db.applications.update_one(
        {"_id": ObjectId(application_id)},
        {
            "$set": {
                "status": "accepted",
                "stage": "offer_sent"
            },
            "$push": {
                "timeline": {
                    "event": "Candidate Selected",
                    "timestamp": datetime.now(),
                    "description": "Candidate was selected for this position"
                }
            }
        }
    )

    # ── Email notification: accepted ──
    if NOTIFICATIONS_AVAILABLE:
        try:
            cand_email = application.get('candidateEmail', '')
            cand_name = application.get('candidateName', 'Candidate')
            j = db.jobs.find_one({'_id': application['jobId']})
            job_title = j.get('title', 'the position') if j else 'the position'
            send_application_accepted(cand_email, cand_name, job_title)
        except Exception as _e:
            print(f"[WARN] Accepted email failed: {_e}")
    
    # Reject all other applications for this job
    other_apps = list(db.applications.find({
        "jobId": job_id,
        "_id": {"$ne": ObjectId(application_id)},
        "status": {"$ne": "rejected"}
    }))

    db.applications.update_many(
        {
            "jobId": job_id,
            "_id": {"$ne": ObjectId(application_id)},
            "status": {"$ne": "rejected"}
        },
        {
            "$set": {
                "status": "rejected",
                "stage": "position_filled"
            },
            "$push": {
                "timeline": {
                    "event": "Application Rejected",
                    "timestamp": datetime.now(),
                    "description": "Position was filled by another candidate"
                }
            }
        }
    )

    # ── Email notification: rejection for other candidates ──
    if NOTIFICATIONS_AVAILABLE:
        for other_app in other_apps:
            try:
                rej_email = other_app.get('candidateEmail', '')
                rej_name = other_app.get('candidateName', 'Candidate')
                j = db.jobs.find_one({'_id': job_id})
                job_title = j.get('title', 'the position') if j else 'the position'
                send_application_rejected(rej_email, rej_name, job_title)
            except Exception as _e:
                print(f"[WARN] Rejection email failed for {rej_email}: {_e}")
    
    # Update job status to filled
    db.jobs.update_one(
        {"_id": job_id},
        {
            "$set": {
                "status": "filled",
                "selectedCandidateId": str(candidate_id),
                "closedAt": datetime.now()
            }
        }
    )
    
    return jsonify({
        "message": "Candidate selected successfully",
        "jobStatus": "filled"
    })

@applications_bp.route('/<application_id>/accept', methods=['POST'])
def accept_candidate(application_id):
    """Accept a candidate and close the job"""
    if not ObjectId.is_valid(application_id):
        return jsonify({"error": "Invalid application ID"}), 400
    
    application = db.applications.find_one({"_id": ObjectId(application_id)})
    if not application:
        return jsonify({"error": "Application not found"}), 404
    
    job_id = application['jobId']
    candidate_id = application['candidateId']
    
    # Update the application to accepted
    db.applications.update_one(
        {"_id": ObjectId(application_id)},
        {
            "$set": {
                "status": "accepted",
                "stage": "offer_sent"
            },
            "$push": {
                "timeline": {
                    "event": "Candidate Accepted",
                    "timestamp": datetime.now(),
                    "description": "Application was accepted and job was closed"
                }
            }
        }
    )

    # ── Email notification: accepted ──
    if NOTIFICATIONS_AVAILABLE:
        try:
            cand_email = application.get('candidateEmail', '')
            cand_name = application.get('candidateName', 'Candidate')
            j = db.jobs.find_one({'_id': application['jobId']})
            job_title = j.get('title', 'the position') if j else 'the position'
            send_application_accepted(cand_email, cand_name, job_title)
        except Exception as _e:
            print(f"[WARN] Accepted email failed: {_e}")
    
    # Reject all other applications for this job
    other_apps = list(db.applications.find({
        "jobId": job_id,
        "_id": {"$ne": ObjectId(application_id)},
        "status": {"$ne": "rejected"}
    }))

    db.applications.update_many(
        {
            "jobId": job_id,
            "_id": {"$ne": ObjectId(application_id)},
            "status": {"$ne": "rejected"}
        },
        {
            "$set": {
                "status": "rejected",
                "stage": "position_filled"
            },
            "$push": {
                "timeline": {
                    "event": "Application Rejected",
                    "timestamp": datetime.now(),
                    "description": "Position was filled by another candidate"
                }
            }
        }
    )

    # ── Email notification: rejection for other candidates ──
    if NOTIFICATIONS_AVAILABLE:
        for other_app in other_apps:
            try:
                rej_email = other_app.get('candidateEmail', '')
                rej_name = other_app.get('candidateName', 'Candidate')
                j = db.jobs.find_one({'_id': job_id})
                job_title = j.get('title', 'the position') if j else 'the position'
                send_application_rejected(rej_email, rej_name, job_title)
            except Exception as _e:
                print(f"[WARN] Rejection email failed for {rej_email}: {_e}")
    
    # Close the job
    db.jobs.update_one(
        {"_id": job_id},
        {
            "$set": {
                "status": "filled",
                "selectedCandidateId": str(candidate_id),
                "closedAt": datetime.now()
            }
        }
    )
    
    return jsonify({
        "message": "Candidate accepted and job closed successfully",
        "jobStatus": "filled"
    })

@applications_bp.route('/<application_id>/reject', methods=['POST'])
def reject_candidate(application_id):
    """Reject a candidate"""
    if not ObjectId.is_valid(application_id):
        return jsonify({"error": "Invalid application ID"}), 400
    
    application = db.applications.find_one({"_id": ObjectId(application_id)})
    if not application:
        return jsonify({"error": "Application not found"}), 404
    
    db.applications.update_one(
        {"_id": ObjectId(application_id)},
        {
            "$set": {
                "status": "rejected",
                "stage": "rejected"
            },
            "$push": {
                "timeline": {
                    "event": "Application Rejected",
                    "timestamp": datetime.now(),
                    "description": "Application was rejected"
                }
            }
        }
    )

    # ── Email notification: rejected ──
    if NOTIFICATIONS_AVAILABLE:
        try:
            cand_email = application.get('candidateEmail', '')
            cand_name = application.get('candidateName', 'Candidate')
            j = db.jobs.find_one({'_id': application['jobId']})
            job_title = j.get('title', 'the position') if j else 'the position'
            send_application_rejected(cand_email, cand_name, job_title)
        except Exception as _e:
            print(f"[WARN] Rejected email failed: {_e}")

    return jsonify({"message": "Candidate rejected successfully"})

@applications_bp.route('/<application_id>/remove-status', methods=['POST'])
def remove_status(application_id):
    """Remove accepted/rejected status and reopen the application"""
    if not ObjectId.is_valid(application_id):
        return jsonify({"error": "Invalid application ID"}), 400
    
    application = db.applications.find_one({"_id": ObjectId(application_id)})
    if not application:
        return jsonify({"error": "Application not found"}), 404
    
    job_id = application['jobId']
    
    # Update the application back to under_review
    db.applications.update_one(
        {"_id": ObjectId(application_id)},
        {
            "$set": {
                "status": "under_review",
                "stage": "review"
            },
            "$push": {
                "timeline": {
                    "event": "Status Removed",
                    "timestamp": datetime.now(),
                    "description": "Application status was reset to under review"
                }
            }
        }
    )
    
    # If job was filled, check if there are any other accepted candidates
    # If not, reopen the job
    job = db.jobs.find_one({"_id": job_id})
    if job and job.get('status') == 'filled':
        # Check if there are any other accepted applications
        other_accepted = db.applications.find_one({
            "jobId": job_id,
            "_id": {"$ne": ObjectId(application_id)},
            "status": "accepted"
        })
        
        if not other_accepted:
            # Reopen the job
            db.jobs.update_one(
                {"_id": job_id},
                {
                    "$set": {"status": "open"},
                    "$unset": {"selectedCandidateId": "", "closedAt": ""}
                }
            )
            return jsonify({
                "message": "Status removed and job reopened",
                "jobStatus": "open"
            })
    
    return jsonify({"message": "Status removed successfully"})

/// Custom exception for API errors with structured error information.
///
/// Backend returns errors in the format:
/// ```json
/// {
///   "error": {
///     "code": "ErrorClassName",
///     "message": "Human readable description",
///     "details": {...}
///   }
/// }
/// ```
class ApiException implements Exception {
  final String code;
  final String message;
  final int statusCode;
  final Map<String, dynamic> details;

  const ApiException({
    required this.code,
    required this.message,
    required this.statusCode,
    this.details = const {},
  });

  /// Parse ApiException from backend error response
  factory ApiException.fromJson(Map<String, dynamic> json, int statusCode) {
    // Standard FastAPI error format: {"detail": "Error message"}
    if (json.containsKey('detail')) {
      final detail = json['detail'];
      final message = detail is List
          ? (detail.first['msg'] ?? 'Validation Error')
          : detail.toString();
      return ApiException(
        code: 'FastAPIError',
        message: message,
        statusCode: statusCode,
      );
    }

    // Custom IMKException format: {"error": {"code": "...", "message": "...", "details": {...}}}
    final error = json['error'] as Map<String, dynamic>? ?? {};
    return ApiException(
      code: error['code'] as String? ?? 'UnknownError',
      message: error['message'] as String? ?? 'An unknown error occurred',
      statusCode: statusCode,
      details: error['details'] as Map<String, dynamic>? ?? {},
    );
  }

  /// Generic network error (no response from server)
  factory ApiException.networkError([String? message]) {
    return ApiException(
      code: 'NetworkError',
      message: message ?? 'Network error - please check your connection',
      statusCode: 0,
    );
  }

  /// Generic timeout error
  factory ApiException.timeout() {
    return const ApiException(
      code: 'TimeoutError',
      message: 'Request timed out - please try again',
      statusCode: 0,
    );
  }

  /// Unexpected error (e.g., invalid JSON)
  factory ApiException.unexpected(String message) {
    return ApiException(
      code: 'UnexpectedError',
      message: message,
      statusCode: 0,
    );
  }

  // Convenience getters for common error types
  bool get isNotFound => statusCode == 404;
  bool get isUnauthorized => statusCode == 401;
  bool get isForbidden => statusCode == 403;
  bool get isValidationError => statusCode == 400;
  bool get isServerError => statusCode >= 500;
  bool get isNetworkError => statusCode == 0;

  @override
  String toString() => 'ApiException($code): $message';

  /// User-friendly error message for display
  String get userMessage {
    // For common errors, provide friendlier messages
    if (isNetworkError) {
      return 'Network error. Please check your connection and try again.';
    }
    if (isUnauthorized) {
      return 'Session expired. Please log in again.';
    }
    if (isForbidden) {
      return 'You do not have permission to perform this action.';
    }
    if (isServerError) {
      return 'Server error. Please try again later.';
    }
    // Otherwise use the backend message
    return message;
  }
}

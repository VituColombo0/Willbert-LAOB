/***********************************************************************
FaceImage - Foto (rosto) projetada sobre a areia do AR Sandbox. A cor de
cada ponto vem da foto, deformada pela altura da areia ("lente" que
amplia onde a areia está alta) e sombreada pelo relevo.
Parte do Willbert (modo Rosto); não existe no SARndbox original.
***********************************************************************/

#ifndef FACEIMAGE_INCLUDED
#define FACEIMAGE_INCLUDED

#include <GL/gl.h>
#include <GL/GLObject.h>
#include <Images/RGBImage.h>

#include "Types.h"

class FaceImage:public GLObject
	{
	/* Embedded classes: */
	private:
	struct DataItem:public GLObject::DataItem
		{
		public:
		GLuint textureObjectId;
		DataItem(void);
		virtual ~DataItem(void);
		};
	
	/* Elements: */
	Images::RGBImage image; // A foto
	GLfloat faceTransformMatrix[16]; // Espaço da câmera -> (u, v, altura em cm, 1), coluna-major
	
	public:
	GLfloat params[4]; // ampliação por cm, deslocamento por cm, mistura com as cores de relevo, força da sombra
	GLfloat reference; // altura de referência (mediana da areia), em cm
	
	/* Constructors and destructors: */
	FaceImage(const char* imageFileName);
	
	/* Methods from class GLObject: */
	virtual void initContext(GLContextData& contextData) const;
	
	/* New methods: */
	void setTransform(const OGTransform& cameraToBox,const double boxMin[2],const double boxMax[2],int rotation,bool mirror);
	void bindTexture(GLContextData& contextData) const;
	void uploadUniforms(GLint transformLocation,GLint paramsLocation,GLint referenceLocation) const;
	};

#endif
